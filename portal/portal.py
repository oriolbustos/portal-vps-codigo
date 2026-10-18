#!/usr/bin/env python3
"""Portal personal en https://testobm.duckdns.org/ (puerto 8791, detrás de Caddy).

- Login propio (usuario + contraseña con hash bcrypt en /etc/portal/clave) y cookie de
  sesión que recuerda el dispositivo 30 días. Frena los intentos repetidos por IP y en global.
- /_auth: lo consulta Caddy (forward_auth) antes de dejar pasar a /finanzas/ (la llave).
- Portada con una tarjeta por panel (lista PANELES) y /entreno/, que se rehace solo
  cuando cambian las notas diarias del vault (/entreno/clasico/ = versión anterior).
- /estilo/: guía de estilo viva. Las reglas se leen de la nota del vault ESTILO_NOTA;
  la hoja compartida y las fuentes se sirven en /estatico/ (sin sesión).
- Modo censurado por dispositivo (botón Censurar de la portada) y /config/: ver censura.py.
"""
import hashlib, html, json, os, re, secrets, sys, threading, time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit

import bcrypt

sys.path.insert(0, str(Path(__file__).parent))
import censura, entreno, listas, tareas, todo, trabajo  # noqa: E402
import entreno_sesion  # noqa: E402  (Entreno en directo, /entreno/sesion/)
import medidas  # noqa: E402  (Entreno: Perímetros y Fotos)

USUARIO = "oriol"
CLAVE = Path(os.environ.get("PORTAL_CLAVE", "/etc/portal/clave"))
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
SESIONES = ESTADO / "sesiones.json"
PANEL_ENTRENO = ESTADO / "entreno.html"
PANEL_ENTRENO_CLASICO = ESTADO / "entreno-clasico.html"
AQUI = Path(__file__).parent
ESTATICO = AQUI / "estatico"
ESTILO_NOTA = Path("/srv/vault/30 - Personal/00 - Sistema VPS/estilo-frontend.md")
TIPOS = {".css": "text/css; charset=utf-8", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
PUERTO = int(os.environ.get("PORTAL_PUERTO", "8791"))
DOMINIO = os.environ.get("PORTAL_DOMINIO", "testobm.duckdns.org")
COOKIE = "portal_sesion"
DURACION = 30 * 86400          # la sesión recuerda el dispositivo 30 días
RENOVAR = 15 * 86400           # si queda menos de esto, se renueva al usarla
CAJA = Path("/srv/finanzas/abierto")
PASOS_TOKEN = Path("/etc/portal/pasos-token")       # lo usa el Atajo del iPhone (cabecera Authorization: Bearer)
PASOS_IPHONE = ESTADO / "pasos-iphone.json"         # lo lee /opt/salud-sync/sincronizar.py

# Freno de intentos: por IP, 5 fallos en 15 min → 15 min bloqueada.
# En global, 20 fallos en 15 min → login cerrado 15 min para todos (las sesiones abiertas siguen).
VENTANA, MAX_IP, MAX_TOTAL, BLOQUEO = 900, 5, 20, 900
fallos_ip: dict[str, list[float]] = {}
fallos_total: list[float] = []
bloqueo_total = [0.0]
cerrojo = threading.Lock()


def log(msg):
    print(msg, flush=True)


# ---------- sesiones ----------
def cargar_sesiones() -> dict:
    try:
        return json.loads(SESIONES.read_text())
    except (OSError, ValueError):
        return {}


def guardar_sesiones(s: dict):
    tmp = SESIONES.with_suffix(".tmp")
    tmp.write_text(json.dumps(s))
    os.chmod(tmp, 0o600)
    tmp.replace(SESIONES)


def huella_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def nueva_sesion(ua: str) -> str:
    token = secrets.token_urlsafe(32)
    with cerrojo:
        s = {k: v for k, v in cargar_sesiones().items() if v["hasta"] > time.time()}
        s[huella_token(token)] = {"hasta": time.time() + DURACION, "creada": time.time(), "ua": ua[:120]}
        guardar_sesiones(s)
    return token


def sesion_valida(token: str, renovar=True):
    """→ (válida, token renovado o None). Solo renueva si la respuesta llega al navegador."""
    if not token:
        return False, None
    with cerrojo:
        s = cargar_sesiones()
        v = s.get(huella_token(token))
        if not v or v["hasta"] < time.time():
            return False, None
        if renovar and v["hasta"] - time.time() < RENOVAR:
            v["hasta"] = time.time() + DURACION
            guardar_sesiones(s)
            return True, token
    return True, None


def censurado(token: str) -> bool:
    """¿Esta sesión está en modo censurado? (botón Censurar; se sale cerrando sesión)."""
    with cerrojo:
        v = cargar_sesiones().get(huella_token(token or ""))
    return bool(v and v.get("censurado"))


def censurar(token: str):
    with cerrojo:
        s = cargar_sesiones()
        v = s.get(huella_token(token or ""))
        if v and not v.get("censurado"):
            v["censurado"] = True
            guardar_sesiones(s)


def descensurar(token: str):
    with cerrojo:
        s = cargar_sesiones()
        v = s.get(huella_token(token or ""))
        if v and v.pop("censurado", None):
            guardar_sesiones(s)


def borrar_sesion(token: str):
    with cerrojo:
        s = cargar_sesiones()
        if s.pop(huella_token(token or ""), None):
            guardar_sesiones(s)


# Fotos de Entreno: en modo censurado no existen nunca, aunque Entreno se vea (medidas.py pregunta aquí).
medidas.privado = lambda h: censurado(h.token())


def sin_fotos(pagina: str) -> str:
    """Entreno en modo censurado: quita la pestaña Fotos sin dejar hueco (los datos ya llegan vacíos)."""
    pagina = pagina.replace('<a href="#fotos">Fotos</a>', "").replace('"perimetros", "fotos"]', '"perimetros"]')
    return pagina.replace("</head>", "<style>#v-fotos{display:none!important}</style></head>", 1)


# ---------- contraseña y freno ----------
def comprobar(usuario: str, clave: str) -> bool:
    try:
        guardado = CLAVE.read_text().strip().encode()
    except OSError:
        log("portal: no puedo leer /etc/portal/clave")
        return False
    ok_clave = bcrypt.checkpw(clave.encode()[:72], guardado) if guardado.startswith(b"$2") else False
    return secrets.compare_digest(usuario.strip().lower(), USUARIO) and ok_clave


def bloqueado(ip: str) -> int:
    """Segundos que le quedan de bloqueo (0 = puede intentar)."""
    ahora = time.time()
    with cerrojo:
        if bloqueo_total[0] > ahora:
            return int(bloqueo_total[0] - ahora)
        f = [t for t in fallos_ip.get(ip, []) if t > ahora - VENTANA]
        fallos_ip[ip] = f
        if len(f) >= MAX_IP:
            return int(f[-1] + BLOQUEO - ahora)
    return 0


def apuntar_fallo(ip: str):
    ahora = time.time()
    with cerrojo:
        fallos_ip.setdefault(ip, []).append(ahora)
        fallos_total[:] = [t for t in fallos_total if t > ahora - VENTANA] + [ahora]
        if len(fallos_total) >= MAX_TOTAL:
            bloqueo_total[0] = ahora + BLOQUEO
            log(f"portal: {len(fallos_total)} fallos en 15 min, login cerrado 15 min")
        for k in [k for k, v in fallos_ip.items() if not v or v[-1] < ahora - VENTANA]:
            del fallos_ip[k]
    log(f"portal: login fallido desde {ip}")


# ---------- panel de entreno ----------
estado_entreno = {"firma": None, "error": "", "ultimo": None}


def firma_diario():
    n, m = 0, 0.0
    for p in entreno.DIARIO.glob("*/*/*.md"):
        n += 1
        m = max(m, p.stat().st_mtime)
    for p in [entreno.PLAN_NUTRICION, *entreno.HISTORICO.glob("*.md")]:  # objetivos y "Estado actual" (sección Hoy)
        try:
            m = max(m, p.stat().st_mtime)
        except OSError:
            pass
    return n, m


def rehacer_entreno(forzar=False):
    try:
        firma = firma_diario()
        if not forzar and firma == estado_entreno["firma"] and PANEL_ENTRENO.exists():
            return
        if not forzar and time.time() - firma[1] < 5:  # aún escribiendo
            return
        datos = entreno.construir()
        tmp = PANEL_ENTRENO.with_suffix(".tmp")
        tmp.write_text(entreno.html(datos), encoding="utf-8")
        tmp.replace(PANEL_ENTRENO)
        tmp.write_text(entreno.html(datos, "entreno-clasico.html"), encoding="utf-8")
        tmp.replace(PANEL_ENTRENO_CLASICO)
        pesos = [d for d in datos["dias"] if d.get("peso")]
        estado_entreno.update(firma=firma, error="", ultimo={
            "fecha": datos["dias"][-1]["fecha"] if datos["dias"] else None,
            "peso": pesos[-1]["peso"] if pesos else None, "peso_fecha": pesos[-1]["fecha"] if pesos else None})
        log(f"portal: panel de entreno rehecho ({firma[0]} notas)")
    except Exception as e:
        estado_entreno["error"] = str(e)
        log(f"portal: error al rehacer entreno: {e}")


def vigilante():
    while True:
        rehacer_entreno()
        time.sleep(30)


# ---------- paneles de la portada ----------
def estado_finanzas():
    try:
        return "Caja abierta" if os.path.ismount(CAJA) else "Caja cerrada"
    except OSError:
        return ""


# Para añadir un panel: una entrada más aquí (y su ruta en Caddy o en este servicio).
# "desc": muy breve, estilo ejecutivo. "estado": solo si de verdad hace falta verlo desde la portada
# (hoy solo la caja de Finanzas); los trozos separados por " · " salen como insignias.
PANELES = [
    {"titulo": "To Do", "ruta": "/todo/", "desc": "Mi día, listas y pendientes.", "estado": None},
    {"titulo": "Tareas", "ruta": "/tareas/", "desc": "Pendientes por ámbito y prioridad.", "estado": None},
    {"titulo": "Listas", "ruta": "/listas/", "desc": "Compra, regalos, deseos y más.", "estado": None,
     "desc_censurado": "Compra y otras listas."},  # en modo censurado, si hay listas ocultas
    {"titulo": "Finanzas", "ruta": "/finanzas/", "desc": "Gastos, ingresos y patrimonio.", "estado": estado_finanzas},
    {"titulo": "Entreno", "ruta": "/entreno/", "desc": "Peso, nutrición, actividad y fuerza.", "estado": None},
    {"titulo": "Trabajo", "ruta": "/trabajo/", "desc": "CV actual.", "estado": None},
    {"titulo": "Estilo", "ruta": "/estilo/", "desc": "Guía visual y de escritura.", "estado": None},
    {"titulo": "Configuración", "ruta": "/config/", "desc": "Modo censurado y ajustes.", "estado": None},  # en modo censurado, solo "Volver al modo normal"
]

# ---------- pasos del iPhone (Atajo de iOS) ----------
def leer_pasos_texto(texto: str) -> dict:
    """Líneas 'AAAA-MM-DD  12.345' (o JSON {fecha: pasos}) → {fecha: int}."""
    try:
        d = json.loads(texto)
        if isinstance(d, dict):
            texto = "\n".join(f"{k} {v}" for k, v in d.items())
    except ValueError:
        pass
    out = {}
    for linea in texto.splitlines():
        m = re.search(r"(\d{4}-\d{2}-\d{2})\D+?([\d.,]+)", linea)
        if not m:
            continue
        n = m.group(2).rstrip(".,")
        if re.fullmatch(r"\d{1,3}([.,]\d{3})+", n):
            n = re.sub(r"[.,]", "", n)
        try:
            v = int(float(n.replace(",", ".")))
        except ValueError:
            continue
        if 0 <= v <= 100000:
            out[m.group(1)] = max(v, out.get(m.group(1), 0))
    return out


def guardar_pasos(nuevos: dict) -> int:
    with cerrojo:
        try:
            datos = json.loads(PASOS_IPHONE.read_text())
        except (OSError, ValueError):
            datos = {}
        for f, v in nuevos.items():
            datos[f] = {"pasos": v, "recibido": datetime.now().strftime("%Y-%m-%d %H:%M")}
        datos = dict(sorted(datos.items())[-120:])
        tmp = PASOS_IPHONE.with_suffix(".tmp")
        tmp.write_text(json.dumps(datos, ensure_ascii=False))
        os.chmod(tmp, 0o640)
        tmp.replace(PASOS_IPHONE)
    return len(nuevos)


# ---------- HTML ----------
# Estilo compartido: /opt/portal/estatico/estilo.css (guía en /estilo/). Sin emojis en los textos.
CABEZA = """<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f7f5f2" media="(prefers-color-scheme: light)">
<meta name="theme-color" content="#151413" media="(prefers-color-scheme: dark)">
<link rel="icon" href="/estatico/iconos/fav-__ICONO__.svg" type="image/svg+xml">
<link rel="stylesheet" href="/estatico/estilo.css">
<style>
.login { min-height: 92vh; display: grid; place-items: center; padding: 16px; }
.login form { width: 100%; max-width: 360px; padding: 24px; }
.login h1 { font-family: var(--serif); font-size: var(--t-3xl); line-height: 1; }
.tarjetas { grid-template-columns: repeat(auto-fill, minmax(min(100%, 300px), 1fr)); margin-top: 32px; }
@media (min-width: 768px) { .tarjetas .tarjeta { min-height: 190px; } }
</style>"""


def pagina(titulo, cuerpo, icono="portal"):
    cabeza = CABEZA.replace("__ICONO__", icono)
    return f'<!doctype html><html lang="es"><head><meta charset="utf-8"><title>{titulo}</title>{cabeza}</head><body>{cuerpo}</body></html>'


def pagina_login(siguiente="/", error=""):
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return pagina("Portal · entrar", f"""<main class="login"><div class="rejilla" style="width:100%;max-width:362px"><form method="post" action="/login">
<h1>Portal</h1>{err}
<input type="hidden" name="next" value="{html.escape(siguiente)}">
<label class="campo"><span class="etiqueta">Usuario</span><input name="usuario" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required></label>
<label class="campo"><span class="etiqueta">Contraseña</span><input type="password" name="clave" autocomplete="current-password" required></label>
<button class="boton fuerte ancho">Entrar</button></form></div></main>""")


def pagina_portada(cens=False):
    tarjetas = []
    conf = censura.leer()
    for p in PANELES:
        if cens and censura.panel_oculto(p["ruta"], conf):
            continue
        try:
            est = p["estado"]() if p.get("estado") else ""
        except Exception:
            est = ""
        insignias = "".join(
            f'<span class="insignia{" punto" if re.match(r"(Error|Fitbit)", t) else ""}">{html.escape(t)}</span>'
            for t in est.split(" · ") if t)
        ico = p["ruta"].strip("/")  # el icono se llama como la ruta: /estatico/iconos/<ruta>.svg
        quita = {"finanzas": conf["finanzas"], "listas": conf["listas_ocultas"]}.get(ico)
        desc = p["desc_censurado"] if cens and quita and p.get("desc_censurado") else p["desc"]
        tarjetas.append(f"""<div class="tarjeta"><h2><span class="sello"><i class="ico ico-{ico}"></i></span><a class="principal" href="{p['ruta']}">{html.escape(p['titulo'])}</a></h2>
<p class="secundario peq">{html.escape(desc)}</p>{f'<div class="pie">{insignias}</div>' if insignias else ''}</div>""")
    return pagina("Portal", f"""<div class="pagina"><header class="cabecera">
<div class="marca"><h1>Portal</h1></div>
<div class="extra">{'' if cens else '<form method="post" action="/censurar"><button class="boton">Censurar</button></form>'}<form method="post" action="/salir"><button class="boton">Cerrar sesión</button></form></div></header>
<div class="rejilla tarjetas">{''.join(tarjetas)}</div>
</div>""")


def pagina_trabajo(elegido="", editar=False):
    estilo = """<style>.barra-cv { display: flex; flex-wrap: wrap; gap: 12px; align-items: center; justify-content: space-between; padding: 24px 0 8px; }
.barra-cv form, .fila-botones { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.editor { display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); gap: 1px; padding: 1px; margin-top: 8px; }
.editor > * { box-shadow: 0 0 0 1px var(--linea); background: var(--fondo); min-width: 0; }
.editor textarea { width: 100%; height: calc(100vh - 220px); min-height: 420px; resize: vertical; border: 0; padding: 16px; font: 300 13px/1.55 var(--mono);
  color: var(--tinta-alta); background: var(--fondo-alto); tab-size: 2; white-space: pre; overflow: auto; }
.editor textarea:focus { outline: none; box-shadow: inset 0 0 0 1px var(--linea-alta); }
.previa { padding: 16px; height: calc(100vh - 220px); min-height: 420px; overflow: auto; }
.previa img { display: block; width: 100%; background: #fff; box-shadow: 0 0 0 1px var(--linea); margin-bottom: 16px; }
.previa pre { white-space: pre-wrap; margin-top: 12px; }
@media (max-width: 900px) { .editor { grid-template-columns: 1fr; } .editor textarea, .previa { height: 60vh; } }
.barra-cv select { font: inherit; font-size: var(--t-sm); padding: 4px 8px; background: var(--fondo-alto); color: var(--tinta-alta); border: 1px solid var(--linea); border-radius: 6px; }
.fuente { margin-bottom: 16px; }
.hojas img { display: block; width: 100%; max-width: 900px; margin: 0 auto 24px; background: #fff; box-shadow: 0 0 0 1px var(--linea); }</style>"""
    return pagina("Trabajo", icono="trabajo", cuerpo=f"""{estilo}<div class="pagina"><header class="cabecera">
<div class="marca"><a class="volver" href="/">← Portal</a><h1><span class="sello"><i class="ico ico-trabajo"></i></span>Trabajo</h1></div>
<nav class="nav"><a class="aqui" href="/trabajo/">CV</a></nav></header>
{trabajo.editor(elegido) if editar else trabajo.pagina(elegido)}</div>""")


# ---------- guía de estilo ----------
def md_linea(t: str) -> str:
    t = html.escape(t, quote=False)
    t = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda m: m.group(2) or m.group(1), t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"`(.+?)`", r"<code>\1</code>", t)
    return re.sub(r"(?<![\w/])(https://[^\s<,)]+)", r'<a href="\1">\1</a>', t)


def md_bloques(lineas: list) -> str:
    """Markdown mínimo: ### títulos, listas con '-', párrafos, **negrita**, `código`."""
    out, lista, parrafo = [], [], []

    def cerrar():
        if lista:
            out.append("<ul>" + "".join(f"<li>{x}</li>" for x in lista) + "</ul>")
            lista.clear()
        if parrafo:
            out.append("<p>" + " ".join(parrafo) + "</p>")
            parrafo.clear()
    for l in lineas:
        l = l.rstrip()
        if not l.strip():
            cerrar()
        elif l.startswith("### "):
            cerrar()
            out.append(f"<h3>{md_linea(l[4:])}</h3>")
        elif l.lstrip().startswith("- "):
            if parrafo:
                cerrar()
            lista.append(md_linea(l.lstrip()[2:]))
        else:
            if lista:
                cerrar()
            parrafo.append(md_linea(l.strip()))
    cerrar()
    return "\n".join(out)


def pagina_estilo() -> str:
    plantilla = (AQUI / "estilo.html").read_text(encoding="utf-8")
    try:
        texto = ESTILO_NOTA.read_text(encoding="utf-8")
    except OSError:
        texto = "No encuentro la nota de estilo en el vault."
    if texto.startswith("---"):
        fin = texto.find("\n---", 3)
        texto = texto[fin + 4:] if fin >= 0 else texto
    partes, actual = {"intro": []}, "intro"
    for l in texto.splitlines():
        if l.startswith("# "):
            continue
        if l.startswith("## "):
            actual = l[3:].strip()
            partes[actual] = []
            continue
        partes[actual].append(l)
    return re.sub(r"<!--md:(.+?)-->", lambda m: md_bloques(partes.get(m.group(1), [])), plantilla)


def pagina_config(guardado=False):
    paneles = [(p["ruta"].strip("/").split("/")[0], p["titulo"]) for p in PANELES if p["ruta"] != "/config/"]
    try:
        nombres_listas = [listas.NOMBRES.get(p.stem, p.stem) for p in listas.notas()]
    except OSError:
        nombres_listas = []
    cuerpo = censura.pagina(censura.leer(), paneles, [n for n, _ in tareas.ambitos()], todo.datos(), nombres_listas, guardado)
    return pagina("Configuración", cuerpo, icono="config")


def con_datos(plantilla: Path, datos) -> str:
    """Como tareas.html() / todo.html(), pero con datos ya filtrados (modo censurado)."""
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)


NO_ENCONTRADO = '<div class="pagina"><p class="vacio">No existe esta página. <a class="enlace" href="/">Volver al portal</a></p></div>'


def siguiente_seguro(s: str) -> str:
    """Solo rutas internas: evita redirecciones a otros dominios."""
    if not s or not s.startswith("/") or s.startswith("//") or "\\" in s:
        return "/"
    return s


# ---------- servidor ----------
class Manejador(BaseHTTPRequestHandler):
    server_version = "portal"
    sys_version = ""

    def log_message(self, *a):
        pass

    def ip(self):
        xff = self.headers.get("X-Forwarded-For", "")
        return xff.split(",")[-1].strip() or self.client_address[0]

    def token(self):
        for trozo in (self.headers.get("Cookie") or "").split(";"):
            k, _, v = trozo.strip().partition("=")
            if k == COOKIE:
                return v
        return ""

    def cabeceras_comunes(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")

    def cookie(self, token, max_age=DURACION):
        self.send_header("Set-Cookie", f"{COOKIE}={token}; Max-Age={max_age}; Path=/; HttpOnly; Secure; SameSite=Lax")

    def enviar(self, cuerpo: str, codigo=200, renovado=None):
        datos = cuerpo.encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.cabeceras_comunes()
        if renovado:
            self.cookie(renovado)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(datos)

    def redirigir(self, destino, token=None, borrar=False, codigo=303):
        self.send_response(codigo)
        self.send_header("Location", destino)
        self.send_header("Content-Length", "0")
        self.cabeceras_comunes()
        if token:
            self.cookie(token)
        if borrar:
            self.cookie("", 0)
        self.end_headers()

    def a_login(self, siguiente):
        self.redirigir("/login?next=" + quote(siguiente_seguro(siguiente), safe="/"), codigo=302)

    def do_HEAD(self):
        self.do_GET()

    def no_encontrado(self):
        self.enviar(pagina("No encontrado", NO_ENCONTRADO), 404)

    def enviar_json(self, d, codigo=200):
        datos = json.dumps(d, ensure_ascii=False).encode()
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(datos)))
        self.cabeceras_comunes()
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self):
        ruta = urlsplit(self.path).path
        if ruta == "/_auth":  # Caddy forward_auth
            # Caddy no pasa al navegador las cabeceras de un 200, así que aquí no se renueva.
            if not sesion_valida(self.token(), renovar=False)[0]:
                return self.a_login(self.headers.get("X-Forwarded-Uri", "/"))
            cens = censurado(self.token())
            if cens and censura.panel_oculto(urlsplit(self.headers.get("X-Forwarded-Uri", "/")).path, censura.leer()):
                return self.no_encontrado()
            self.send_response(200)
            self.send_header("X-Portal-Censura", "1" if cens else "0")  # Caddy la copia a la llave (copy_headers)
            self.send_header("Content-Length", "0")
            return self.end_headers()
        if ruta.startswith("/estatico/"):  # hoja de estilo y fuentes: públicas
            return self.estatico(ruta[len("/estatico/"):])
        if ruta == "/login":
            siguiente = siguiente_seguro(parse_qs(urlsplit(self.path).query).get("next", ["/"])[0])
            if sesion_valida(self.token())[0]:
                return self.redirigir(siguiente)
            return self.enviar(pagina_login(siguiente))
        ok, renovado = sesion_valida(self.token())
        if not ok:
            return self.a_login(self.path)
        cens = censurado(self.token())
        conf = censura.leer() if cens else None
        if cens and censura.panel_oculto(ruta, conf):
            return self.no_encontrado()
        if ruta == "/":
            return self.enviar(pagina_portada(cens), renovado=renovado)
        if ruta == "/config":
            return self.redirigir("/config/", codigo=301)
        if ruta == "/config/":
            if cens:
                return self.enviar(pagina("Configuración", censura.pagina_censurado(), icono="config"), renovado=renovado)
            return self.enviar(pagina_config("guardado" in urlsplit(self.path).query), renovado=renovado)
        if ruta.startswith(("/entreno/medidas/", "/entreno/fotos/")) and medidas.get(self, ruta):
            return
        if ruta == "/entreno/sesion":
            return self.redirigir("/entreno/sesion/", codigo=301)
        if ruta == "/entreno/sesion/":
            return self.enviar(entreno_sesion.html(AQUI / "entreno-sesion.html"), renovado=renovado)
        if ruta == "/entreno":
            return self.redirigir("/entreno/", codigo=301)
        if ruta in ("/entreno/", "/entreno/index.html"):
            rehacer_entreno()
            if not PANEL_ENTRENO.exists():
                return self.enviar(pagina("Entreno", f"<div class='pagina'><p class='vacio'>Aún no hay panel. {html.escape(estado_entreno['error'])}</p></div>"), 503)
            panel = PANEL_ENTRENO.read_text(encoding="utf-8")
            return self.enviar(sin_fotos(panel) if cens else panel, renovado=renovado)
        if ruta in ("/entreno/clasico", "/entreno/clasico/"):
            rehacer_entreno()
            if not PANEL_ENTRENO_CLASICO.exists():
                rehacer_entreno(forzar=True)
            if PANEL_ENTRENO_CLASICO.exists():
                return self.enviar(PANEL_ENTRENO_CLASICO.read_text(encoding="utf-8"), renovado=renovado)
        if ruta == "/estilo":
            return self.redirigir("/estilo/", codigo=301)
        if ruta == "/estilo/":
            return self.enviar(pagina_estilo(), renovado=renovado)
        if ruta in ("/tareas", "/trabajo", "/listas"):
            return self.redirigir(ruta + "/", codigo=301)
        if ruta == "/listas/":
            if cens and conf["listas_ocultas"]:
                d = {"listas": censura.listas(listas.todas(), conf), "generado": time.strftime("%H:%M")}
                return self.enviar(con_datos(AQUI / "listas.html", d), renovado=renovado)
            return self.enviar(listas.html(AQUI / "listas.html"), renovado=renovado)
        if ruta == "/tareas/":
            if cens:
                return self.enviar(con_datos(AQUI / "tareas.html", censura.tareas(tareas.datos(), conf)), renovado=renovado)
            return self.enviar(tareas.html(AQUI / "tareas.html"), renovado=renovado)
        if ruta == "/todo":
            return self.redirigir("/todo/", codigo=301)
        if ruta == "/todo/":
            if cens:
                return self.enviar(con_datos(AQUI / "todo.html", censura.todo(todo.datos(), conf)), renovado=renovado)
            return self.enviar(todo.html(AQUI / "todo.html"), renovado=renovado)
        if ruta in ("/todo/datos", "/tareas/datos"):
            d = (todo if ruta == "/todo/datos" else tareas).datos()
            if cens:
                d = (censura.todo if ruta == "/todo/datos" else censura.tareas)(d, conf)
            return self.enviar_json(d)
        if ruta in ("/tareas/matriz", "/tareas/matriz/"):  # dirección vieja (unas horas del 2026-10-18)
            return self.redirigir("/tareas/", codigo=301)
        if ruta == "/trabajo/":
            elegido = parse_qs(urlsplit(self.path).query).get("cv", [""])[0]
            return self.enviar(pagina_trabajo(elegido), renovado=renovado)
        if ruta == "/trabajo/editar":
            elegido = parse_qs(urlsplit(self.path).query).get("cv", [""])[0]
            return self.enviar(pagina_trabajo(elegido, editar=True), renovado=renovado)
        m = re.fullmatch(r"/trabajo/cv/([0-9a-f]{16})/([\w.-]+)", ruta)
        if m and (f := trabajo.archivo(m.group(1), m.group(2))):
            return self.enviar_archivo(f, "application/pdf" if f.suffix == ".pdf" else "image/png")
        self.no_encontrado()

    def estatico(self, nombre: str):
        """Solo archivos de /opt/portal/estatico con extensión conocida, sin salir de la carpeta."""
        ruta = (ESTATICO / nombre).resolve()
        if ESTATICO.resolve() not in ruta.parents or ruta.suffix not in TIPOS or not ruta.is_file():
            return self.enviar("No encontrado", 404)
        # Las fuentes casi nunca cambian; la hoja de estilo, sí: que el navegador pregunte siempre.
        self.enviar_archivo(ruta, TIPOS[ruta.suffix], "public, max-age=86400" if ruta.suffix == ".woff2" else "no-cache")

    def enviar_archivo(self, ruta: Path, tipo: str, cache="private, max-age=3600"):
        datos = ruta.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(datos)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(datos)

    def do_POST(self):
        if urlsplit(self.path).path == "/api/pasos":
            return self.api_pasos()
        origen = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if origen and urlsplit(origen).hostname != DOMINIO:
            return self.enviar("Origen no permitido", 403)
        if censurado(self.token()) and (censura.panel_oculto(urlsplit(self.path).path, censura.leer())
                                         or urlsplit(self.path).path.startswith("/entreno/fotos/")):  # fotos: nunca
            return self.no_encontrado()
        if urlsplit(self.path).path == "/config/":
            if censurado(self.token()):  # en modo censurado no se cambia nada
                return self.redirigir("/config/")
            return self.api_config()
        if medidas.post(self, urlsplit(self.path).path, lambda: sesion_valida(self.token(), renovar=False)[0]):
            return
        if urlsplit(self.path).path == "/entreno/sesion/guardar":
            return self.api_entreno_sesion()
        if urlsplit(self.path).path == "/tareas/operar":
            return self.api_tareas()
        if urlsplit(self.path).path == "/listas/operar":
            return self.api_listas()
        if urlsplit(self.path).path == "/todo/operar":
            return self.api_todo()
        if urlsplit(self.path).path in ("/trabajo/compilar", "/trabajo/guardar"):
            return self.api_cv(urlsplit(self.path).path.rsplit("/", 1)[1])
        largo = min(int(self.headers.get("Content-Length") or 0), 4096)
        campos = parse_qs(self.rfile.read(largo).decode("utf-8", "replace"))
        ruta = urlsplit(self.path).path
        if ruta == "/login":
            ip = self.ip()
            siguiente = siguiente_seguro(campos.get("next", ["/"])[0])
            espera = bloqueado(ip)
            if espera:
                return self.enviar(pagina_login(siguiente, f"Demasiados intentos. Prueba otra vez en {espera // 60 + 1} min."), 429)
            if comprobar(campos.get("usuario", [""])[0], campos.get("clave", [""])[0]):
                with cerrojo:
                    fallos_ip.pop(ip, None)
                log(f"portal: login correcto desde {ip}")
                return self.redirigir(siguiente, token=nueva_sesion(self.headers.get("User-Agent", "")))
            apuntar_fallo(ip)
            time.sleep(1)
            return self.enviar(pagina_login(siguiente, "Usuario o contraseña incorrectos."), 401)
        if ruta == "/salir":
            borrar_sesion(self.token())
            return self.redirigir("/login", borrar=True)
        if ruta == "/descensurar":  # vuelta al modo normal desde /config/, sin contraseña (lo pidió Oriol)
            if sesion_valida(self.token(), renovar=False)[0]:
                descensurar(self.token())
                log(f"portal: modo censurado desactivado desde {self.ip()}")
            return self.redirigir("/")
        if ruta == "/censurar":  # modo censurado en este dispositivo; se sale en /config/ o con /salir
            if sesion_valida(self.token(), renovar=False)[0]:
                censurar(self.token())
                log(f"portal: modo censurado activado desde {self.ip()}")
            return self.redirigir("/")
        self.enviar("No encontrado", 404)

    def api_config(self):
        """Formulario de /config/ → /var/lib/portal/config.json (solo fuera del modo censurado)."""
        if not sesion_valida(self.token(), renovar=False)[0]:
            return self.a_login("/config/")
        largo = min(int(self.headers.get("Content-Length") or 0), 32768)
        campos = parse_qs(self.rfile.read(largo).decode("utf-8", "replace"))
        censura.guardar(censura.desde_formulario(campos, censura.leer()))
        log("portal: configuración guardada")
        self.redirigir("/config/?guardado")

    def api_entreno_sesion(self):
        """Entreno en directo: {fecha, huella, filas} → tabla Entreno de la nota del día vía entreno-sesion-guardar."""
        def responder(codigo, d):
            datos = json.dumps(d, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.cabeceras_comunes()
            self.end_headers()
            self.wfile.write(datos)
        if not sesion_valida(self.token(), renovar=False)[0]:
            return responder(401, {"ok": False, "mensaje": "Sesión caducada. Vuelve a entrar."})
        largo = min(int(self.headers.get("Content-Length") or 0), 131072)
        try:
            d = json.loads(self.rfile.read(largo))
        except ValueError:
            return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        codigo, r = entreno_sesion.operar(d)
        if r.get("ok"):
            log(f"portal: entreno en directo: {len(d.get('filas') or [])} series en {d.get('fecha')}")
        responder(codigo, r)

    def api_tareas(self):
        """Panel Tareas: {op: crear|editar|borrar, ambito, base, ...} → se aplica en la nota del ámbito vía tareas-guardar."""
        def responder(codigo, d):
            datos = json.dumps(d, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.cabeceras_comunes()
            self.end_headers()
            self.wfile.write(datos)
        if not sesion_valida(self.token(), renovar=False)[0]:
            return responder(401, {"ok": False, "mensaje": "Sesión caducada. Vuelve a entrar."})
        largo = min(int(self.headers.get("Content-Length") or 0), 16384)
        try:
            d = json.loads(self.rfile.read(largo))
        except ValueError:
            return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        if censurado(self.token()):  # las tareas redactadas llevan id falso: tareas.operar no las encuentra
            conf = censura.leer()
            codigo, r = tareas.operar(d)
            if isinstance(r.get("datos"), dict):
                r["datos"] = censura.tareas(r["datos"], conf)
        else:
            codigo, r = tareas.operar(d)
        if r.get("ok"):
            log(f"portal: tareas: {d.get('op')} en {d.get('ambito')}")
        responder(codigo, r)

    def api_listas(self):
        """Panel Listas: {archivo, op: anadir|marcar|archivar, ...} → se aplica en el vault vía listas-guardar."""
        def responder(codigo, d):
            datos = json.dumps(d, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.cabeceras_comunes()
            self.end_headers()
            self.wfile.write(datos)
        if not sesion_valida(self.token(), renovar=False)[0]:
            return responder(401, {"ok": False, "mensaje": "Sesión caducada. Vuelve a entrar."})
        largo = min(int(self.headers.get("Content-Length") or 0), 8192)
        try:
            d = json.loads(self.rfile.read(largo))
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        if censurado(self.token()):
            ocultas = set(censura.leer()["listas_ocultas"])
            if any(listas.NOMBRES.get(p.stem, p.stem) in ocultas and p.name == d.get("archivo") for p in listas.notas()):
                return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        codigo, r = listas.operar(d)
        if r.get("ok"):
            log(f"portal: lista {d.get('archivo')}: {d.get('op')}")
        responder(codigo, r)

    def api_todo(self):
        """Panel To Do: {op, base, ...} → se aplica sobre la nota y se guarda en el vault vía todo-guardar."""
        def responder(codigo, d):
            datos = json.dumps(d, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.cabeceras_comunes()
            self.end_headers()
            self.wfile.write(datos)
        if not sesion_valida(self.token(), renovar=False)[0]:
            return responder(401, {"ok": False, "mensaje": "Sesión caducada. Vuelve a entrar."})
        largo = min(int(self.headers.get("Content-Length") or 0), 16384)
        try:
            d = json.loads(self.rfile.read(largo))
        except ValueError:
            return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        if censurado(self.token()):
            conf = censura.leer()
            if not isinstance(d, dict) or not censura.todo_permitida(d, todo.datos(), conf):
                return responder(409, {"ok": False, "mensaje": "Esa lista ya no está; recarga.", "datos": censura.todo(todo.datos(), conf)})
            codigo, r = todo.operar(d)
            if isinstance(r.get("datos"), dict):
                r["datos"] = censura.todo(r["datos"], conf)
            if r.get("ok"):
                log(f"portal: to do: {d.get('op')}")
            return responder(codigo, r)
        codigo, r = todo.operar(d)
        if r.get("ok"):
            log(f"portal: to do: {d.get('op')}")
        responder(codigo, r)

    def api_cv(self, accion: str):
        """Editor del CV. compilar: {texto} → vista previa. guardar: {cv, base, texto} → al vault vía cv-guardar."""
        def responder(codigo, d):
            datos = json.dumps(d, ensure_ascii=False).encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(datos)))
            self.cabeceras_comunes()
            self.end_headers()
            self.wfile.write(datos)
        if not sesion_valida(self.token(), renovar=False)[0]:
            return responder(401, {"ok": False, "mensaje": "Sesión caducada. Copia tus cambios y vuelve a entrar."})
        largo = int(self.headers.get("Content-Length") or 0)
        if largo > 400_000:
            return responder(413, {"ok": False, "mensaje": "Demasiado grande."})
        try:
            d = json.loads(self.rfile.read(largo))
            texto = d["texto"]
            if not isinstance(texto, str):
                raise ValueError
        except (ValueError, KeyError, TypeError):
            return responder(400, {"ok": False, "mensaje": "Petición no válida."})
        if accion == "compilar":
            try:
                c = trabajo.compilar_fuente(texto.replace("\r\n", "\n").encode("utf-8"))
            except Exception as e:
                c = {"error": str(e), "log": "", "paginas": 0, "huella": ""}
            return responder(200, c)
        codigo, r = trabajo.guardar(str(d.get("cv", "")), str(d.get("base", "")), texto)
        if r.get("ok"):
            log(f"portal: CV guardado ({d.get('cv')})")
        responder(codigo, r)

    def api_pasos(self):
        def texto(cuerpo, codigo):
            d = cuerpo.encode()
            self.send_response(codigo)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)
        ip = self.ip()
        if bloqueado(ip):
            return texto("demasiados intentos", 429)
        try:
            esperado = PASOS_TOKEN.read_text().strip()
        except OSError:
            return texto("sin token en el servidor", 503)
        dado = (self.headers.get("Authorization") or "").removeprefix("Bearer").strip()
        if not esperado or not secrets.compare_digest(dado, esperado):
            apuntar_fallo(ip)
            return texto("token incorrecto", 401)
        largo = min(int(self.headers.get("Content-Length") or 0), 16384)
        nuevos = leer_pasos_texto(self.rfile.read(largo).decode("utf-8", "replace"))
        if not nuevos:
            return texto("no he entendido ningún día (formato: AAAA-MM-DD pasos, uno por línea)", 400)
        n = guardar_pasos(nuevos)
        log(f"portal: pasos del iPhone recibidos ({n} días)")
        texto(f"ok, {n} días", 200)


if __name__ == "__main__":
    ESTADO.mkdir(parents=True, exist_ok=True)
    threading.Thread(target=vigilante, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PUERTO), Manejador).serve_forever()
