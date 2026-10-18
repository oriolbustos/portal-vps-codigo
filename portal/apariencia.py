"""Apariencia del portal, editable desde /config/ (pestañas Apariencia y Portada).

- Guarda en /var/lib/portal/apariencia.json solo lo que cambia respecto a fábrica. Sin el archivo = fábrica.
  "Restablecer de fábrica" lo borra (y borra los iconos y fuentes subidos).
- Se aplica sin tocar las páginas:
  * /estatico/estilo.css se sirve con un bloque añadido al final (css()) que pisa los tokens de color y fuentes.
    El selector html:root:root:root gana a los :root de las plantillas (p. ej. --a1..--a6 de Tareas).
  * Modo claro u oscuro fijo: en el CSS y en cada HTML que sirve el portal, "(prefers-color-scheme: dark)"
    pasa a "all" o "not all" (modo()). Así también siguen las gráficas que miran matchMedia.
  * Iconos: /estatico/iconos/<panel>.svg y fav-<panel>.svg se sirven desde aquí si se cambiaron (icono()).
- Subidos (iconos SVG/PNG y fuentes): /var/lib/portal/apariencia/<id>.<ext>, servidos en /estatico/propio/.
  Los SVG se limpian (solo formas y atributos de dibujo; nada de scripts, enlaces ni estilos).
"""
import base64, hashlib, json, os, re, secrets, shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
ARCHIVO = ESTADO / "apariencia.json"   # copia cada hora en el backup del vault (.portal-estado/)
SUBIDOS = ESTADO / "apariencia"
ICONOS = Path(__file__).parent / "estatico" / "iconos"

COLOR = re.compile(r"#[0-9a-f]{6}")
# Tokens de estilo.css (y --a1..--a6 de Tareas). Valor de fábrica = el que se ve sin cambiar nada.
COLORES_MODO = [  # cada modo tiene los suyos
    ("fondo", "Fondo", "página"), ("fondo-alto", "Fondo alto", "tarjeta activa, campos"), ("fondo-2", "Relleno", "zonas suaves"),
    ("tinta-alta", "Tinta alta", "títulos y cifras"), ("tinta", "Tinta", "texto"), ("tinta-2", "Tinta 2", "texto secundario"),
    ("linea", "Línea", "bordes de 1 px"),
]
FABRICA_CLARO = {"fondo": "#f7f5f2", "fondo-alto": "#ffffff", "fondo-2": "#e2dfdb", "tinta-alta": "#1f1d1d", "tinta": "#4a4846",
                 "tinta-2": "#726f6d", "linea": "#ccc9c5"}
FABRICA_OSCURO = {"fondo": "#151413", "fondo-alto": "#1d1c1b", "fondo-2": "#262524", "tinta-alta": "#f7f5f2", "tinta": "#e2dfdb",
                  "tinta-2": "#a09d9a", "linea": "#383634"}
COLORES_COMUNES = [  # iguales en los dos modos
    ("acento", "Acento", "el punto de \"aquí\" y los avisos"),
    ("c1", "Gráfica 1", "azul pizarra"), ("c2", "Gráfica 2", "rojo"), ("c3", "Gráfica 3", "verde azulado"), ("c4", "Gráfica 4", "ocre"),
    ("a1", "Ámbito 1", "bolitas de Tareas"), ("a2", "Ámbito 2", "bolitas de Tareas"), ("a3", "Ámbito 3", "bolitas de Tareas"),
    ("a4", "Ámbito 4", "bolitas de Tareas"), ("a5", "Ámbito 5", "bolitas de Tareas"), ("a6", "Ámbito 6", "bolitas de Tareas"),
]
FABRICA_COMUN = {"acento": "#d95151", "c1": "#3f6ea8", "c2": "#d95151", "c3": "#24906f", "c4": "#a8740a",
                 "a1": "#3f6ea8", "a2": "#24906f", "a3": "#a8740a", "a4": "#7a5ea8", "a5": "#8a5a3c", "a6": "#6f7f22"}

FUENTES = {  # clave → (nombre, pila CSS)
    "instrument-serif": ("Instrument Serif", '"Instrument Serif", "Georgia ajustada", Georgia, serif'),
    "instrument-sans": ("Instrument Sans", '"Instrument Sans", system-ui, -apple-system, "Segoe UI", sans-serif'),
    "jetbrains-mono": ("JetBrains Mono", '"JetBrains Mono", ui-monospace, Consolas, monospace'),
    "georgia": ("Georgia", 'Georgia, "Times New Roman", serif'),
    "sistema": ("Del sistema", 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'),
    "helvetica": ("Helvetica / Arial", '"Helvetica Neue", Helvetica, Arial, sans-serif'),
    "mono-sistema": ("Monoespaciada del sistema", 'ui-monospace, "SF Mono", Consolas, monospace'),
}
HUECOS_FUENTE = {"titulos": ("--serif", "Títulos y cifras grandes"), "texto": ("--sans", "Texto"), "numeros": ("--mono", "Números de tablas y ejes")}
FABRICA_FUENTES = {"titulos": "instrument-serif", "texto": "instrument-sans", "numeros": "jetbrains-mono"}

FABRICA = {"modo": "auto", "claro": {}, "oscuro": {}, "comun": {}, "fuentes": {}, "escala": 100, "nombre": "Portal",
           "logo_en_titulo": False, "iconos": {}, "paneles": {"orden": [], "ocultos": []}, "subidos": {}}
MODOS = ("auto", "claro", "oscuro")


# ---------- leer y guardar ----------
def leer() -> dict:
    try:
        d = json.loads(ARCHIVO.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError
    except (OSError, ValueError):
        d = {}
    return limpiar(d)


def limpiar(d: dict) -> dict:
    """Cualquier json → apariencia válida (lo raro se descarta y queda el valor de fábrica)."""
    a = json.loads(json.dumps(FABRICA))
    if not isinstance(d, dict):
        return a
    if d.get("modo") in MODOS:
        a["modo"] = d["modo"]
    for k, fab in (("claro", FABRICA_CLARO), ("oscuro", FABRICA_OSCURO), ("comun", FABRICA_COMUN)):
        v = d.get(k) if isinstance(d.get(k), dict) else {}
        a[k] = {t: c.lower() for t, c in v.items() if t in fab and isinstance(c, str) and COLOR.fullmatch(c.lower()) and c.lower() != fab[t]}
    sub = d.get("subidos") if isinstance(d.get("subidos"), dict) else {}
    for i, s in sub.items():
        if (re.fullmatch(r"[0-9a-f]{16}", i) and isinstance(s, dict) and s.get("tipo") in ("icono", "fuente")
                and s.get("ext") in EXT_ICONO + EXT_FUENTE and (SUBIDOS / f"{i}.{s['ext']}").is_file()):
            a["subidos"][i] = {"tipo": s["tipo"], "ext": s["ext"], "nombre": str(s.get("nombre", ""))[:60]}
    f = d.get("fuentes") if isinstance(d.get("fuentes"), dict) else {}
    for h in HUECOS_FUENTE:
        v = f.get(h)
        if v in FUENTES and v != FABRICA_FUENTES[h]:
            a["fuentes"][h] = v
        elif isinstance(v, str) and v.startswith("sub:") and a["subidos"].get(v[4:], {}).get("tipo") == "fuente":
            a["fuentes"][h] = v
    try:
        a["escala"] = max(80, min(130, int(d.get("escala", 100))))
    except (TypeError, ValueError):
        pass
    n = d.get("nombre")
    if isinstance(n, str) and n.strip():
        a["nombre"] = n.strip()[:40]
    a["logo_en_titulo"] = d.get("logo_en_titulo") is True
    ic = d.get("iconos") if isinstance(d.get("iconos"), dict) else {}
    for panel, v in ic.items():
        if not (isinstance(panel, str) and re.fullmatch(r"[a-z0-9-]{1,30}", panel) and isinstance(v, str)):
            continue
        if v.startswith("fab:") and v[4:] in iconos_fabrica() and v[4:] != panel:
            a["iconos"][panel] = v
        elif v.startswith("sub:") and a["subidos"].get(v[4:], {}).get("tipo") == "icono":
            a["iconos"][panel] = v
    p = d.get("paneles") if isinstance(d.get("paneles"), dict) else {}
    for k in ("orden", "ocultos"):
        v = p.get(k) if isinstance(p.get(k), list) else []
        a["paneles"][k] = list(dict.fromkeys(x for x in v if isinstance(x, str) and re.fullmatch(r"[a-z0-9-]{1,30}", x)))
    a["paneles"]["ocultos"] = [x for x in a["paneles"]["ocultos"] if x != "config"]  # Configuración siempre se ve
    return a


def guardar(d: dict) -> dict:
    a = limpiar(d)
    usados = {v[4:] for v in list(a["iconos"].values()) + list(a["fuentes"].values()) if v.startswith("sub:")}
    for i in list(a["subidos"]):  # un subido que ya no usa nadie se borra
        if i not in usados:
            (SUBIDOS / f"{i}.{a['subidos'][i]['ext']}").unlink(missing_ok=True)
            del a["subidos"][i]
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o640)
    tmp.replace(ARCHIVO)
    return a


def de_fabrica():
    ARCHIVO.unlink(missing_ok=True)
    shutil.rmtree(SUBIDOS, ignore_errors=True)


def opciones(paneles: list) -> dict:
    """Lo que necesita la página para pintar los controles. paneles = [(clave, título)]."""
    return {
        "colores_modo": COLORES_MODO, "colores_comunes": COLORES_COMUNES,
        "fabrica": {"claro": FABRICA_CLARO, "oscuro": FABRICA_OSCURO, "comun": FABRICA_COMUN, "fuentes": FABRICA_FUENTES},
        "fuentes": {k: n for k, (n, _) in FUENTES.items()}, "huecos_fuente": {k: t for k, (_, t) in HUECOS_FUENTE.items()},
        "iconos": iconos_fabrica(), "paneles": [{"clave": k, "titulo": t} for k, t in paneles],
    }


# ---------- CSS ----------
def _bloque(sel, d):
    return f"{sel} {{ {' '.join(f'--{k}: {v};' for k, v in d.items())} }}\n" if d else ""


def css(a: dict) -> str:
    """Bloque que se añade al final de estilo.css. Vacío si todo está de fábrica."""
    r = "html:root:root:root"
    out = []
    for h, v in a["fuentes"].items():
        if v.startswith("sub:"):
            s = a["subidos"][v[4:]]
            formato = {"woff2": "woff2", "woff": "woff", "ttf": "truetype", "otf": "opentype"}[s["ext"]]
            out.append(f'@font-face {{ font-family: "F{v[4:]}"; src: url("propio/{v[4:]}.{s["ext"]}") format("{formato}"); font-display: swap; }}\n')
    fuentes = {}
    for h, v in a["fuentes"].items():
        pila = f'"F{v[4:]}", {FUENTES[FABRICA_FUENTES[h]][1]}' if v.startswith("sub:") else FUENTES[v][1]
        fuentes[HUECOS_FUENTE[h][0][2:]] = pila
    out.append(_bloque(r, {**a["comun"], **fuentes}))
    if a["claro"]:
        out.append("@media (prefers-color-scheme: light) { " + _bloque(f'{r}:not([data-theme="dark"])', a["claro"]) + "}\n")
        out.append(_bloque(f'{r}[data-theme="light"]', a["claro"]))
    if a["oscuro"]:
        out.append("@media (prefers-color-scheme: dark) { " + _bloque(f'{r}:not([data-theme="light"])', a["oscuro"]) + "}\n")
        out.append(_bloque(f'{r}[data-theme="dark"]', a["oscuro"]))
    for panel, v in a["iconos"].items():  # ?v= para que el navegador no siga con el icono anterior
        out.append(f'.ico-{panel} {{ --i: url("iconos/{panel}.svg?v={hashlib.sha256(v.encode()).hexdigest()[:8]}"); }}\n')
    if a["escala"] != 100:
        out.append(f"html {{ font-size: {a['escala']}%; }}\n")
    texto = "".join(out)
    return f"\n/* ---------- apariencia elegida en /config/ (apariencia.py) ---------- */\n{texto}" if texto.strip() else ""


OSCURO = re.compile(r"\(\s*prefers-color-scheme\s*:\s*dark\s*\)")
CLARO = re.compile(r"\(\s*prefers-color-scheme\s*:\s*light\s*\)")


def modo(texto: str, a: dict) -> str:
    """Modo fijo: las consultas de modo oscuro (CSS, <meta>, matchMedia) pasan a ser siempre ciertas o falsas."""
    if a["modo"] == "auto":
        return texto
    oscuro = a["modo"] == "oscuro"
    texto = OSCURO.sub("all" if oscuro else "not all", texto)
    return CLARO.sub("not all" if oscuro else "all", texto)


# ---------- iconos ----------
def iconos_fabrica() -> list:
    """Iconos de línea disponibles: los de los paneles y los de iconos/extra/."""
    nombres = [p.stem for p in sorted(ICONOS.glob("*.svg")) if not p.stem.startswith("fav-")]
    return nombres + [f"extra/{p.stem}" for p in sorted((ICONOS / "extra").glob("*.svg"))]


def favicon(datos: bytes, tipo: str) -> bytes:
    """Favicon (cuadrado oscuro con el icono en claro) a partir de cualquier icono: lo usa como máscara."""
    b64 = base64.b64encode(datos).decode()
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><mask id="m" style="mask-type:alpha">'
            f'<image href="data:{tipo};base64,{b64}" x="5" y="5" width="22" height="22"/></mask>'
            f'<rect width="32" height="32" fill="#1f1d1d"/><rect width="32" height="32" fill="#f7f5f2" mask="url(#m)"/></svg>').encode()


def icono(nombre: str, a: dict):
    """nombre = "<panel>.svg" o "fav-<panel>.svg" → (bytes, tipo) si ese panel tiene icono cambiado; si no, None."""
    m = re.fullmatch(r"(fav-)?([a-z0-9-]+)\.svg", nombre)
    if not m or m.group(2) not in a["iconos"]:
        return None
    fav, v = bool(m.group(1)), a["iconos"][m.group(2)]
    if v.startswith("fab:"):
        n = v[4:]
        if fav and (ICONOS / f"fav-{n}.svg").is_file():
            return (ICONOS / f"fav-{n}.svg").read_bytes(), "image/svg+xml"
        datos, tipo = (ICONOS / f"{n}.svg").read_bytes(), "image/svg+xml"
    else:
        s = a["subidos"][v[4:]]
        datos, tipo = (SUBIDOS / f"{v[4:]}.{s['ext']}").read_bytes(), "image/png" if s["ext"] == "png" else "image/svg+xml"
    return (favicon(datos, tipo), "image/svg+xml") if fav else (datos, tipo)


# ---------- subir ----------
EXT_ICONO = ["svg", "png"]
EXT_FUENTE = ["woff2", "woff", "ttf", "otf"]
SVG_NS = "http://www.w3.org/2000/svg"
ETIQUETAS = {"svg", "g", "path", "circle", "ellipse", "line", "polyline", "polygon", "rect", "title", "desc"}
ATRIBUTOS = {"viewBox", "width", "height", "d", "cx", "cy", "r", "rx", "ry", "x", "y", "x1", "y1", "x2", "y2", "points",
             "fill", "fill-rule", "fill-opacity", "clip-rule", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
             "stroke-miterlimit", "stroke-dasharray", "stroke-opacity", "opacity", "transform"}


def limpiar_svg(datos: bytes) -> bytes:
    """Solo formas y atributos de dibujo. Lo demás (scripts, enlaces, estilos, imágenes) se quita."""
    texto = datos.decode("utf-8")
    if re.search(r"<!(DOCTYPE|ENTITY)", texto, re.I):
        raise ValueError("el SVG trae DOCTYPE o entidades")
    raiz = ET.fromstring(texto)

    def copiar(el):
        etiqueta = el.tag.split("}")[-1]
        if etiqueta not in ETIQUETAS:
            return None
        nuevo = ET.Element(etiqueta)
        for k, v in el.attrib.items():
            k = k.split("}")[-1]
            if k in ATRIBUTOS and not re.search(r"url\s*\(|javascript:|[<>]", v, re.I):
                nuevo.set(k, v)
        if etiqueta in ("title", "desc"):
            return None
        for hijo in el:
            c = copiar(hijo)
            if c is not None:
                nuevo.append(c)
        return nuevo
    limpio = copiar(raiz)
    if limpio is None or limpio.tag != "svg":
        raise ValueError("no es un SVG")
    if "viewBox" not in limpio.attrib:
        try:
            limpio.set("viewBox", f"0 0 {float(limpio.get('width', '24').rstrip('px'))} {float(limpio.get('height', '24').rstrip('px'))}")
        except ValueError:
            limpio.set("viewBox", "0 0 24 24")
    limpio.attrib.pop("width", None)
    limpio.attrib.pop("height", None)
    limpio.set("xmlns", SVG_NS)
    return ET.tostring(limpio, encoding="utf-8", xml_declaration=False)


def subir(tipo: str, nombre: str, datos: bytes) -> dict:
    """Guarda un icono (SVG limpio o PNG) o una fuente → {id, ext, nombre}. No lo usa todavía: eso lo decide guardar()."""
    if tipo == "icono":
        if datos[:8] == b"\x89PNG\r\n\x1a\n":
            ext = "png"
        else:
            datos, ext = limpiar_svg(datos), "svg"
    elif tipo == "fuente":
        ext = {b"wOF2": "woff2", b"wOFF": "woff", b"\x00\x01\x00\x00": "ttf", b"true": "ttf", b"OTTO": "otf"}.get(datos[:4])
        if not ext:
            raise ValueError("no es una fuente woff2, woff, ttf ni otf")
    else:
        raise ValueError("tipo desconocido")
    i = secrets.token_hex(8)
    SUBIDOS.mkdir(exist_ok=True, mode=0o750)
    (SUBIDOS / f"{i}.{ext}").write_bytes(datos)
    nombre = re.sub(r"\.[a-z0-9]+$", "", nombre.strip(), flags=re.I)[:60] or ext
    # se apunta ya en el json (sin usarlo) para que leer() lo acepte cuando la página lo elija
    a = leer()
    a["subidos"][i] = {"tipo": tipo, "ext": ext, "nombre": nombre}
    tmp = ARCHIVO.with_suffix(".tmp")
    tmp.write_text(json.dumps(a, ensure_ascii=False, indent=1), encoding="utf-8")
    os.chmod(tmp, 0o640)
    tmp.replace(ARCHIVO)
    return {"id": i, "ext": ext, "nombre": nombre, "tipo": tipo}


def subido(nombre: str):
    """/estatico/propio/<id>.<ext> → (ruta, tipo) o None."""
    m = re.fullmatch(r"([0-9a-f]{16})\.(svg|png|woff2|woff|ttf|otf)", nombre)
    if not m or not (SUBIDOS / nombre).is_file():
        return None
    tipos = {"svg": "image/svg+xml", "png": "image/png", "woff2": "font/woff2", "woff": "font/woff", "ttf": "font/ttf", "otf": "font/otf"}
    return SUBIDOS / nombre, tipos[m.group(2)]


# ---------- portada ----------
def ordenar(paneles: list, a: dict, clave) -> list:
    """Orden elegido (los que no salen en él, al final en su orden de siempre) y sin los ocultos."""
    orden = a["paneles"]["orden"]
    pos = {k: i for i, k in enumerate(orden)}
    lista = sorted(paneles, key=lambda p: (pos.get(clave(p), len(orden)), paneles.index(p)))
    return [p for p in lista if clave(p) not in a["paneles"]["ocultos"]]
