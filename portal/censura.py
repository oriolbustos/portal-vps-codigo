"""Modo censurado del portal (para enseñarlo, p. ej. en el trabajo, sin lo personal) y página /config/.

- Se activa por dispositivo: botón "Censurar" de la portada → esa sesión queda marcada ("censurado" en
  sesiones.json). Se sale cerrando sesión y volviendo a entrar con usuario y contraseña.
- Qué se oculta: CONFIG (json), se edita en /config/. En modo censurado /config/ no existe (404) ni su tarjeta.
- Silencioso: lo oculto se quita de los datos antes de enviarlos. Nada en pantalla dice que falte algo.
- Finanzas (misma estructura, cifras como •••): /_auth manda a Caddy la cabecera X-Portal-Censura (1/0), Caddy la pasa a la llave
  (/opt/finanzas/llave.py) y la llave lee la clave "finanzas" de este mismo json.
"""
import hashlib, html, json, os, secrets
from pathlib import Path

ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
CONFIG = ESTADO / "config.json"   # copia cada hora en el backup del vault (.portal-estado/)
POR_DEFECTO = {
    "paneles_ocultos": ["entreno"],                                  # primer trozo de la ruta: /entreno/ → "entreno"
    "ambitos_ocultos": ["Identity", "Health", "Social", "Aspecto"],  # Tareas
    "finanzas": ["patrimonio", "inversiones", "ingresos"],           # bloques que la llave quita (FINANZAS)
    "todo_grupos_ocultos": [],                                       # To Do: grupos enteros
    "todo_listas_ocultas": [],                                       # To Do: [grupo, lista] ("" = sin grupo)
    "listas_ocultas": [],                                            # Listas: nombre de la pestaña
}
FINANZAS = {
    "patrimonio": ("Patrimonio", "Patrimonio e invertido de la portada, su gráfica y los traspasos entre cuentas propias."),
    "inversiones": ("Inversiones", "Valores de la pestaña Inversiones y movimientos de inversión."),
    "ingresos": ("Ingresos y ahorro", "Nómina e ingresos, ahorro en euros y tasa de ahorro."),
}
BANDEJA = "Tareas"  # lista fija de To Do: no se oculta


def leer() -> dict:
    try:
        c = json.loads(CONFIG.read_text(encoding="utf-8"))
        if not isinstance(c, dict):
            raise ValueError
    except (OSError, ValueError):
        c = {}
    return {k: c.get(k, v) if isinstance(c.get(k, v), list) else v for k, v in POR_DEFECTO.items()}


def guardar(c: dict):
    tmp = CONFIG.with_suffix(".tmp")
    tmp.write_text(json.dumps(c, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o640)
    tmp.replace(CONFIG)


def panel_oculto(ruta: str, c: dict) -> bool:
    """¿La ruta pertenece a un panel que no existe en modo censurado? (/config/ sí existe: ahí se vuelve al normal)."""
    trozo = ruta.strip("/").split("/")[0]
    return trozo != "config" and trozo in c["paneles_ocultos"]


# ---------- Tareas: sobre lo que devuelve tareas.datos() ----------
SAL = secrets.token_hex(16)  # cambia en cada arranque: los id tapados no dejan adivinar el texto


def _tapa(id_real: str) -> tuple:
    """→ (id falso estable mientras el portal siga encendido, texto "•••" de largo fijo según ese id)."""
    h = hashlib.sha256((SAL + id_real).encode()).hexdigest()
    return "x" + h[:11], "•" * (5 + int(h[12], 16) % 10)


def tareas(d: dict, c: dict) -> dict:
    """Los ámbitos ocultos siguen saliendo, pero sus tareas van redactadas: texto, subtareas y notas como
    "•••" e id falso (el de verdad es una huella del texto). Importancia, urgencia, esfuerzo y estado se ven.
    Como el id es falso, cualquier cambio sobre ellas falla solo en tareas.operar ("No encuentro esa tarea")."""
    fuera = set(c["ambitos_ocultos"])
    if not isinstance(d, dict) or not fuera:
        return d
    d = dict(d)
    if isinstance(d.get("tareas"), list):
        lista = []
        for x in d["tareas"]:
            if isinstance(x, dict) and x.get("ambito") in fuera:
                x = dict(x)
                x["id"], tapado = _tapa(str(x.get("id", "")))
                for k in ("texto", "crudo"):
                    if k in x:
                        x[k] = tapado
                for k in ("sub", "notas"):
                    if isinstance(x.get(k), list):
                        x[k] = [_tapa(x["id"] + k + str(i))[1] for i in range(len(x[k]))]
            lista.append(x)
        d["tareas"] = lista
    if isinstance(d.get("avisos"), list):
        d["avisos"] = [x for x in d["avisos"] if not any(str(x).startswith(f"{a}:") for a in fuera)]
    return d


# ---------- To Do: sobre lo que devuelve todo.datos() ----------
def _lista_oculta(grupo, titulo, c) -> bool:
    return (grupo or "") in c["todo_grupos_ocultos"] or [grupo or "", titulo] in c["todo_listas_ocultas"]


def todo(d: dict, c: dict) -> dict:
    if not isinstance(d, dict) or not (c["todo_grupos_ocultos"] or c["todo_listas_ocultas"]):
        return d
    d = dict(d)
    d["listas"] = [l for l in d.get("listas", []) if not _lista_oculta(l.get("grupo"), l.get("titulo"), c)]
    d["grupos"] = [g for g in d.get("grupos", []) if g not in c["todo_grupos_ocultos"] and any(l.get("grupo") == g for l in d["listas"])]
    return d


def todo_permitida(op: dict, completo: dict, c: dict) -> bool:
    """completo = todo.datos() sin filtrar (para saber en qué lista está la tarea n)."""
    if not (c["todo_grupos_ocultos"] or c["todo_listas_ocultas"]):
        return True
    grupos = c["todo_grupos_ocultos"]
    for x in (op, op.get("junto") if isinstance(op.get("junto"), dict) else {}):
        g = x.get("grupo") if isinstance(x.get("grupo"), str) else ""
        if g in grupos or (isinstance(x.get("destino"), str) and x["destino"] in grupos):
            return False
        for k in ("lista", "titulo"):
            if isinstance(x.get(k), str) and _lista_oculta(g, x[k], c):
                return False
        if op.get("op", "").startswith("grupo_") and isinstance(x.get("titulo"), str) and x["titulo"] in grupos:
            return False
    n = op.get("n")
    if isinstance(n, int):
        for l in completo.get("listas", []):
            if _lista_oculta(l.get("grupo"), l.get("titulo"), c) and any(t.get("n") == n for t in l.get("tareas", [])):
                return False
    return True


# ---------- Listas ----------
def listas(todas: list, c: dict) -> list:
    return [l for l in todas if l.get("nombre") not in c["listas_ocultas"]]


# ---------- página /config/ ----------
ESTILO = """<style>
.opciones { grid-template-columns: repeat(auto-fill, minmax(min(100%, 280px), 1fr)); }
.opcion { display: flex; gap: 12px; align-items: baseline; padding: 14px 16px; cursor: pointer; }
.opcion input { accent-color: var(--tinta-alta); width: 16px; height: 16px; flex: none; transform: translateY(2px); margin: 0; }
.opcion .secundario { display: block; font-size: var(--t-xs); margin-top: 2px; }
.acciones { display: flex; gap: 12px; align-items: center; flex-wrap: wrap; padding: 32px 0 0; }
.hecho { color: var(--tinta-2); font-size: var(--t-sm); }
</style>"""


def _caja(nombre, valor, marcada, titulo, detalle=""):
    det = f'<span class="secundario">{html.escape(detalle)}</span>' if detalle else ""
    return (f'<label class="opcion"><input type="checkbox" name="{nombre}" value="{html.escape(valor)}"{" checked" if marcada else ""}>'
            f'<span>{html.escape(titulo)}{det}</span></label>')


def _seccion(n, titulo, texto, cajas):
    if not cajas:
        cajas = ['<p class="opcion secundario peq">-</p>']
    return (f'<div class="seccion"><span class="n">{n:02d}</span><h2>{titulo}</h2><p>{texto}</p></div>'
            f'<div class="rejilla opciones">{"".join(cajas)}</div>')


def pagina(c: dict, paneles: list, ambitos: list, todo_datos: dict, nombres_listas: list, guardado=False) -> str:
    """paneles = [(clave, título)], ambitos = [nombre], todo_datos = todo.datos(), nombres_listas = [pestaña]."""
    oc = set(c["paneles_ocultos"])
    s1 = [_caja("panel", k, k in oc, t) for k, t in paneles]
    ams = list(dict.fromkeys(list(ambitos) + list(c["ambitos_ocultos"])))
    s2 = [_caja("ambito", a, a in c["ambitos_ocultos"], a) for a in ams]
    s3 = [_caja("finanzas", k, k in c["finanzas"], t, d) for k, (t, d) in FINANZAS.items()]
    s4 = []
    for g in dict.fromkeys(list(todo_datos.get("grupos", [])) + list(c["todo_grupos_ocultos"])):
        s4.append(_caja("todo_grupo", g, g in c["todo_grupos_ocultos"], g, "Grupo entero"))
    pares = [[l.get("grupo") or "", l.get("titulo")] for l in todo_datos.get("listas", [])
             if not (l.get("titulo") == BANDEJA and not l.get("grupo"))]
    for p in pares + [p for p in c["todo_listas_ocultas"] if p not in pares]:
        s4.append(_caja("todo_lista", json.dumps(p, ensure_ascii=False), p in c["todo_listas_ocultas"], p[1], p[0] and f"Lista de {p[0]}"))
    nls = list(dict.fromkeys(list(nombres_listas) + list(c["listas_ocultas"])))
    s5 = [_caja("lista", n, n in c["listas_ocultas"], n) for n in nls]
    aviso = '<span class="hecho">Guardado.</span>' if guardado else ""
    return f"""{ESTILO}<div class="pagina"><header class="cabecera">
<div class="marca"><a class="volver" href="/">← Portal</a><h1><span class="sello"><i class="ico ico-config"></i></span>Configuración</h1></div>
<div class="extra"><form method="post" action="/censurar"><button class="boton">Censurar</button></form></div></header>
<p class="peq secundario" style="max-width:68ch;padding-top:24px">Modo censurado: para enseñar el portal sin lo personal. Se activa con <b>Censurar</b> en la portada y solo afecta a este dispositivo. Para volver al modo normal, entra aquí y pulsa <b>Volver al modo normal</b> (o cierra sesión y vuelve a entrar).</p>
<form method="post" action="/config/">
{_seccion(1, "Paneles", "Paneles que desaparecen enteros en modo censurado.", s1)}
{_seccion(2, "Ámbitos", "Ámbitos cuyas tareas salen redactadas en Tareas: el ámbito se ve, pero el texto, las subtareas y las notas de sus tareas salen como ••• y no se pueden cambiar.", s2)}
{_seccion(3, "Finanzas", "Cifras que se tapan con ••• en Finanzas (sus gráficas, borrosas). El panel no cambia de forma y gastos, categorías, viajes y suscripciones se ven normal.", s3)}
{_seccion(4, "To Do", "Grupos y listas que se ocultan.", s4)}
{_seccion(5, "Listas", "Pestañas de Listas que se ocultan.", s5)}
<div class="acciones"><button class="boton fuerte">Guardar</button>{aviso}</div>
</form></div>"""


def pagina_censurado() -> str:
    """/config/ en modo censurado: no deja tocar nada, solo volver al modo normal (sin contraseña)."""
    return f"""{ESTILO}<div class="pagina"><header class="cabecera">
<div class="marca"><a class="volver" href="/">← Portal</a><h1><span class="sello"><i class="ico ico-config"></i></span>Configuración</h1></div></header>
<p class="peq secundario" style="max-width:68ch;padding-top:24px">Estás en modo censurado. Para cambiar la configuración, vuelve al modo normal.</p>
<form method="post" action="/descensurar" class="acciones"><button class="boton fuerte">Volver al modo normal</button></form></div>"""


def desde_formulario(campos: dict, c: dict) -> dict:
    """campos = parse_qs del formulario → config nueva (lo que no sale en el formulario se conserva)."""
    nuevo = dict(c)
    nuevo["paneles_ocultos"] = [p for p in campos.get("panel", []) if p and p != "config"]
    nuevo["ambitos_ocultos"] = list(dict.fromkeys(campos.get("ambito", [])))
    nuevo["finanzas"] = [f for f in campos.get("finanzas", []) if f in FINANZAS]
    nuevo["todo_grupos_ocultos"] = list(dict.fromkeys(campos.get("todo_grupo", [])))
    pares = []
    for v in campos.get("todo_lista", []):
        try:
            p = json.loads(v)
        except ValueError:
            continue
        if isinstance(p, list) and len(p) == 2 and all(isinstance(x, str) for x in p) and p not in pares:
            pares.append(p)
    nuevo["todo_listas_ocultas"] = pares
    nuevo["listas_ocultas"] = list(dict.fromkeys(campos.get("lista", [])))
    return nuevo
