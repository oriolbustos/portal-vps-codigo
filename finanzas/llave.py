#!/usr/bin/env python3
"""Llave de la caja de finanzas en el VPS.

- Abre la carpeta cifrada (gocryptfs) con la contraseña que llega del formulario y la cierra sola a las HORAS.
- Con la caja abierta sirve panel.html. El login lo pone el portal (/opt/portal) delante: Caddy
  pregunta a su /_auth (forward_auth) antes de dejar pasar a /finanzas/.
- Cuando cambian datos, reglas o código (Claude por el conector, o un push del PC), rehace panel.html y hace commit.
- Modo censurado del portal (/opt/portal/censura.py): si llega X-Portal-Censura: 1 (la pone el /_auth del portal
  y Caddy la copia), quita del panel lo que diga la clave "finanzas" de /var/lib/portal/config.json.
"""
import html, json, os, subprocess, threading, time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

CIFRADO = Path("/srv/finanzas-cifrado")
ABIERTO = Path("/srv/finanzas/abierto")
REPO = ABIERTO / "panel-finanzas"
HORAS = 12
DOMINIO = "testobm.duckdns.org"
VIGILAR = ["data", "fin", "inbox", "ajustes.yaml", "categorias.yaml", "reglas.yaml"]

estado = {"hasta": None, "huella": None, "error": ""}
cerrojo = threading.Lock()


def abierta() -> bool:
    return os.path.ismount(ABIERTO)


def abrir(clave: str) -> str:
    if abierta():
        return ""
    r = subprocess.run(["gocryptfs", "-q", "-allow_other", "-force_owner", "1000:1000", str(CIFRADO), str(ABIERTO)],
                       input=clave + "\n", text=True, capture_output=True, timeout=60)
    return "" if r.returncode == 0 else "Contraseña incorrecta" if r.returncode == 12 else (r.stderr.strip() or "No se pudo abrir")


def cerrar() -> str:
    """Desmonta la caja. Ojo: fusermount3 da "Permission denied" en este VPS; umount (root) sí va."""
    if abierta():
        if subprocess.run(["umount", str(ABIERTO)], capture_output=True).returncode:
            subprocess.run(["umount", "-l", str(ABIERTO)], capture_output=True)  # ocupada: desmontaje diferido
    estado["hasta"] = None
    estado["huella"] = None
    return "No se pudo cerrar la caja" if abierta() else ""


def huella():
    """Última modificación de lo que afecta al panel."""
    m = 0.0
    for nombre in VIGILAR:
        p = REPO / nombre
        if p.is_file():
            m = max(m, p.stat().st_mtime)
        elif p.is_dir():
            for f in p.rglob("*"):
                if "__pycache__" not in f.parts and f.is_file():
                    m = max(m, f.stat().st_mtime)
    return m


def rehacer():
    subprocess.run(["python3", "-m", "fin.cli", "panel"], cwd=REPO, capture_output=True, timeout=300)
    subprocess.run(["git", "add", "-A"], cwd=REPO, capture_output=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=REPO).returncode:
        subprocess.run(["git", "commit", "-q", "-m", f"VPS: cambios del {datetime.now():%Y-%m-%d %H:%M}"], cwd=REPO, capture_output=True)


def vigilante():
    while True:
        time.sleep(20)
        try:
            with cerrojo:
                if not abierta():
                    estado["hasta"] = estado["huella"] = None
                    continue
                if estado["hasta"] is None:  # abierta por otro lado (o el servicio se reinició)
                    estado["hasta"] = datetime.now() + timedelta(hours=HORAS)
                if datetime.now() >= estado["hasta"]:
                    cerrar()
                    continue
                if not REPO.exists():
                    continue
                h = huella()
                if estado["huella"] is None:
                    estado["huella"] = h
                    if not (REPO / "panel.html").exists():
                        rehacer()
                elif h != estado["huella"] and time.time() - h > 15:  # espera a que acaben de escribir
                    rehacer()
                    estado["huella"] = huella()
        except Exception as e:  # que el vigilante no muera nunca
            estado["error"] = str(e)


# Estilo compartido del portal (/estatico/estilo.css, lo sirve /opt/portal). Sin emojis.
ESTILO = """<meta name=viewport content="width=device-width,initial-scale=1">
<link rel="icon" href="/estatico/iconos/fav-finanzas.svg" type="image/svg+xml"><link rel="stylesheet" href="/estatico/estilo.css"><style>
.login { min-height: 92vh; display: grid; place-items: center; padding: 16px; }
.login form { width: 100%; max-width: 360px; padding: 24px; }
.login h1 { font-family: var(--serif); font-size: var(--t-3xl); line-height: 1; margin-top: 12px; }
.login .volver { font-size: var(--t-sm); color: var(--tinta-2); text-decoration: none; }
</style>"""


def pagina_cerrada(error=""):
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="es"><meta charset="utf-8"><title>Finanzas · cerrado</title>{ESTILO}
<main class="login"><div class="rejilla" style="width:100%;max-width:362px"><form method="post" action="abrir">
<a class="volver" href="/">← Portal</a><h1><span class="sello"><i class="ico ico-finanzas"></i></span>Finanzas</h1>
<p class="secundario peq" style="margin-top:8px">La caja está cerrada. Se abre durante {HORAS} h.</p>{err}
<label class="campo"><span class="etiqueta">Contraseña de la caja</span><input type="password" name="clave" autocomplete="current-password" autofocus></label>
<button class="boton fuerte ancho">Abrir</button></form></div></main></html>"""


ESTILO_PANEL = '<link rel="icon" href="/estatico/iconos/fav-finanzas.svg" type="image/svg+xml"><link rel="stylesheet" href="/estatico/estilo.css"><link rel="stylesheet" href="/estatico/finanzas.css">'


# Vuelta al portal arriba a la izquierda, encima del título del panel.
VOLVER = ('<a href="/" style="color:var(--tinta-2);text-decoration:none;font-size:13px;'
          'width:100%;order:-1">← Portal</a>')


# ---------- modo censurado ----------
CONFIG_PORTAL = Path("/var/lib/portal/config.json")  # la escribe el portal desde /config/
BLOQUES = ["patrimonio", "inversiones", "ingresos"]   # si no se puede leer la config, se quita todo
# Lo que se esconde de cada bloque. Va por posición (tarjetas) y por id: si el panel cambia de estructura, revisar.
CENSURA_CSS = {
    "patrimonio": "#tiles>.tile:nth-child(1),#tiles>.tile:nth-child(4),.card:has(#ch-pat)",
    "inversiones": "#v-inversiones",
    "ingresos": "#tiles>.tile:nth-child(2),.card:has(#ch-aho),#mes-tiles>.tile:nth-child(3),"
                "#mes-tiles>.tile:nth-child(4),.card:has(#mes-ing),#tabla-anual tr:last-child",
}
# Textos del panel que delatarían lo quitado (si ya no están, no pasa nada: los datos ya van vacíos).
CENSURA_TEXTO = {
    "inversiones": [('<a href="#inversiones">Inversiones</a>', ""), ("inversiones: vInversiones, ", "")],
    "ingresos": [('<a href="#mes">Ingresos y gastos</a>', '<a href="#mes">Gastos</a>'),
                 ("<h2>Gastos por grupo e ingresos</h2>", "<h2>Gastos por grupo</h2>"),
                 (', [N, "Ingresos", "line"]', ""),
                 ('lines: [{ name: "Ingresos", color: N, values: meses.map(m => m.ingresos || null) }], ', "")],
}


def censurar(texto: str) -> str:
    """Quita los datos (no solo los esconde) y luego esconde los huecos, sin dejar rastro."""
    try:
        bloques = [b for b in json.loads(CONFIG_PORTAL.read_text()).get("finanzas", BLOQUES) if b in BLOQUES]
    except (OSError, ValueError, AttributeError):
        bloques = BLOQUES
    if not bloques:
        return texto
    i = texto.index("let D = ") + len("let D = ")
    D, fin = json.JSONDecoder().raw_decode(texto, i)
    tipos_fuera = set()
    if "patrimonio" in bloques:
        D["patrimonio"] = [{k: (v if k == "mes" else 0) for k, v in p.items()} for p in D.get("patrimonio", [])]
        tipos_fuera.add("interno")  # traspasos entre cuentas propias: dejan ver saldos
    if "inversiones" in bloques:
        D["inversiones"] = []
        tipos_fuera.add("inversion")
    if "ingresos" in bloques:
        for m in D.get("meses", []):
            m["ingresos"] = 0
        D["ingresos"] = []
    D["detalle"] = [f for f in D.get("detalle", []) if f[8] not in tipos_fuera
                    and not ("ingresos" in bloques and f[8] != "gasto" and f[3] > 0)]  # entradas de dinero (los Bizums de vuelta son "gasto")
    texto = texto[:i] + json.dumps(D, ensure_ascii=False).replace("</", "<\\/") + texto[fin:]
    for b in bloques:
        for viejo, nuevo in CENSURA_TEXTO.get(b, []):
            texto = texto.replace(viejo, nuevo)
    css = ",".join(CENSURA_CSS[b] for b in bloques)
    return texto.replace("</head>", f"<style>{css}{{display:none!important}}</style></head>", 1)


def barra():
    hasta = estado["hasta"]
    cuando = f"Abierta hasta las {hasta:%H:%M}" if hasta else "Abierta"
    return f"""<form method="post" action="cerrar" style="position:fixed;right:12px;bottom:12px;z-index:9999;margin:0;
font:13px var(--sans,system-ui);background:var(--fondo-alto,#fff);color:var(--tinta,#222);border:1px solid var(--linea-alta,#222);
padding:6px 6px 6px 12px;display:flex;gap:10px;align-items:center">{cuando}
<button class="boton" style="font-size:13px">Cerrar</button></form>"""


class Manejador(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def enviar(self, cuerpo: str, codigo=200):
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def volver(self):
        self.send_response(303)
        self.send_header("Location", "./")
        self.end_headers()

    def do_GET(self):
        if self.path not in ("/", "/index.html"):
            return self.enviar("No encontrado", 404)
        panel = REPO / "panel.html"
        if not abierta():
            return self.enviar(pagina_cerrada())
        if not panel.exists():
            return self.enviar(f"<!doctype html><meta charset=utf-8>{ESTILO}<p class='vacio'>Abierta, pero aún no hay panel.html. Recarga en un minuto.</p>{barra()}")
        texto = panel.read_text(encoding="utf-8")
        if self.headers.get("X-Portal-Censura") == "1":
            try:
                texto = censurar(texto)
            except Exception as e:  # si falla, no se enseña nada antes que enseñarlo todo
                print(f"llave: no se pudo censurar: {e}", flush=True)
                return self.enviar(f"<!doctype html><meta charset=utf-8>{ESTILO}<p class='vacio'>El panel no está disponible ahora.</p>", 503)
        # Vuelta al portal arriba a la izquierda, junto al título del panel.
        texto = texto.replace("<h1>Finanzas</h1>", VOLVER + "<h1>Finanzas</h1>", 1)
        # Estilo del portal encima del propio del panel (solo aquí; el panel local no cambia).
        texto = texto.replace("</head>", ESTILO_PANEL + "</head>", 1)
        i = texto.rfind("</body>")
        self.enviar(texto[:i] + barra() + texto[i:] if i >= 0 else texto + barra())

    def do_POST(self):
        origen = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if origen and DOMINIO not in origen:
            return self.enviar("Origen no permitido", 403)
        largo = min(int(self.headers.get("Content-Length") or 0), 4096)
        campos = parse_qs(self.rfile.read(largo).decode("utf-8", "replace"))
        if self.path == "/abrir":
            with cerrojo:
                error = abrir(campos.get("clave", [""])[0])
                if error:
                    return self.enviar(pagina_cerrada(error), 401)
                estado["hasta"] = datetime.now() + timedelta(hours=HORAS)  # abrir otra vez reinicia el contador
            return self.volver()
        if self.path == "/cerrar":
            with cerrojo:
                error = cerrar()
            if error:
                return self.enviar(f"<!doctype html><meta charset=utf-8>{ESTILO}<p class='error' style='padding:16px'>{error}. Prueba otra vez o avisa.</p>", 500)
            return self.volver()
        self.enviar("No encontrado", 404)


if __name__ == "__main__":
    threading.Thread(target=vigilante, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", 8790), Manejador).serve_forever()
