"""Entreno: perímetros (check-ins) y fotos de progreso, para las secciones Perímetros y Fotos del panel.

Perímetros: las notas de check-in del vault, 40 - Entreno/30-checkins/AAAA-MM-DD.md (frontmatter: peso, cuello,
hombros, pecho, cintura, cadera, brazo, brazo_flex, muslo; reglas en 00-sistema/instructions.md §2.9). Los ratios
(hombros/cintura, cintura/altura, cintura/cadera, pecho/cintura, % grasa Navy) se calculan aquí, no se guardan.
Fotos: /srv/entreno-fotos/AAAA-MM-DD/<pose>.jpg, fuera del vault (no van a Obsidian ni a GitHub). Llegan ya
reducidas del navegador (1600 px, JPEG, sin EXIF). Se sirven solo con sesión.

El portal no puede escribir en el vault ni en /srv: deja cada petición en /var/lib/portal/medidas-pendiente/
y medidas-guardar.path lanza /opt/portal/medidas-guardar.py (root).

Rutas (las reparte portal.py): GET /entreno/medidas/datos, GET /entreno/fotos/AAAA-MM-DD/<pose>.jpg,
POST /entreno/medidas/checkin, POST /entreno/fotos/subir?fecha=AAAA-MM-DD&pose=<pose> (también para guardar una
foto recortada o girada: la anterior queda como -antes-), POST /entreno/fotos/mover {fecha, pose, a_fecha, a_pose}
(si el destino tiene foto, se intercambian) y POST /entreno/fotos/eliminar {fecha, pose} (va a .eliminadas/).
"""
import hashlib, json, math, os, re, secrets, time
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import entreno

CHECKINS = entreno.DIARIO.parent / "30-checkins"
PLANTILLA = entreno.DIARIO.parent / "00-sistema" / "plantillas" / "checkin.md"
CONSTANTES = entreno.DIARIO.parent / "10-referencia" / "datos-y-constantes.md"
FOTOS = Path("/srv/entreno-fotos")
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "medidas-pendiente"
RESULTADO = ESTADO / "medidas-resultado.json"

# clave del frontmatter, nombre, rango válido en cm (peso en kg)
CAMPOS = [("peso", "Peso", 40, 200), ("cuello", "Cuello", 25, 70), ("hombros", "Hombros", 70, 180),
          ("pecho", "Pecho", 60, 160), ("cintura", "Cintura", 50, 160), ("cadera", "Cadera", 60, 170),
          ("brazo", "Brazo", 20, 60), ("brazo_flex", "Brazo flex", 20, 65), ("muslo", "Muslo", 35, 90)]
POSES = [("frente", "Frente"), ("lado-izq", "Lado izquierdo"), ("lado-der", "Lado derecho"), ("espalda", "Espalda")]
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MAX_FOTO = 4_000_000


def privado(handler) -> bool:
    """True si la sesión está en modo privado: entonces no se enseñan las fotos. Lo engancha el modo privado."""
    return False


def altura() -> float:
    try:
        m = re.search(r"\|\s*Altura\s*\|\s*(\d+(?:[.,]\d+)?)\s*cm", CONSTANTES.read_text(encoding="utf-8"))
        return float(m.group(1).replace(",", ".")) if m else 190.0
    except OSError:
        return 190.0


def huella(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else ""


def ratios(c: dict, cuello_previo):
    """Ratios de un check-in. Sin cuello se usa el último medido y se marca (cuello_prestado)."""
    v, h = c["v"], altura()
    r = {}
    if v.get("hombros") and v.get("cintura"):
        r["hombros_cintura"] = round(v["hombros"] / v["cintura"], 2)
    if v.get("cintura"):
        r["cintura_altura"] = round(v["cintura"] / h, 2)
    if v.get("cintura") and v.get("cadera"):
        r["cintura_cadera"] = round(v["cintura"] / v["cadera"], 2)
    if v.get("pecho") and v.get("cintura"):
        r["pecho_cintura"] = round(v["pecho"] / v["cintura"], 2)
    cuello = v.get("cuello") or cuello_previo
    if v.get("cintura") and cuello and v["cintura"] > cuello:
        r["navy"] = round(495 / (1.0324 - 0.19077 * math.log10(v["cintura"] - cuello) + 0.15456 * math.log10(h)) - 450, 1)
        r["cuello_prestado"] = not v.get("cuello")
    return r


def checkins() -> list:
    out, cuello = [], None
    for p in sorted(CHECKINS.glob("*.md")):
        if FECHA.match(p.stem) and cuello is None:  # sin cuello anterior se usa el primero que se midió
            try:
                cuello = entreno.num(entreno.frontmatter(p.read_text(encoding="utf-8", errors="replace")).get("cuello"))
            except OSError:
                pass
    cuello_primero, cuello = cuello, None
    for p in sorted(CHECKINS.glob("*.md")):
        if not FECHA.match(p.stem):
            continue
        try:
            fm = entreno.frontmatter(p.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        v = {k: entreno.num(fm.get(k)) for k, *_ in CAMPOS}
        if not any(x is not None for x in v.values()):
            continue
        c = {"fecha": p.stem, "v": {k: x for k, x in v.items() if x is not None},
             "importado": bool(fm.get("origen")), "base": huella(p)}
        c["r"] = ratios(c, cuello or cuello_primero)
        if v.get("cuello"):
            cuello = v["cuello"]
        out.append(c)
    return out


def fotos() -> list:
    out = []
    if not FOTOS.is_dir():
        return out
    for d in sorted(FOTOS.iterdir()):
        if not (d.is_dir() and FECHA.match(d.name)):
            continue
        poses = {}
        for pose, _ in POSES:
            f = d / f"{pose}.jpg"
            if f.is_file():
                poses[pose] = int(f.stat().st_mtime)
        if poses:
            out.append({"fecha": d.name, "poses": poses})
    return out


def datos(handler) -> dict:
    oculto = privado(handler)
    return {"campos": [[k, n] for k, n, *_ in CAMPOS], "poses": POSES, "altura": altura(),
            "checkins": checkins(), "fotos": [] if oculto else fotos(), "fotos_ocultas": oculto}


# ---------- escribir (vía medidas-guardar) ----------
def pedir(peticion: dict, foto: bytes = b""):
    """Deja la petición (y la foto) para medidas-guardar y espera su respuesta → (ok, error)."""
    pid = secrets.token_hex(8)
    peticion["id"] = pid
    PENDIENTE.mkdir(exist_ok=True)
    if foto:  # la foto primero: el escritor solo mira los .json
        tmp = ESTADO / f"medidas-{pid}.jpg.tmp"
        tmp.write_bytes(foto)
        tmp.replace(PENDIENTE / f"{pid}.jpg")
    tmp = ESTADO / f"medidas-{pid}.tmp"
    tmp.write_text(json.dumps(peticion, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(80):  # medidas-guardar.path lo recoge en menos de un segundo
        time.sleep(0.1)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return r.get("ok"), r.get("error", "")
    return False, "El guardado no responde. Revisa medidas-guardar en el servidor."


def fecha_valida(f) -> bool:
    if not isinstance(f, str) or not FECHA.match(f):
        return False
    try:
        d = date.fromisoformat(f)
    except ValueError:
        return False
    return date(2020, 1, 1) <= d <= date.today() + timedelta(days=1)


def valor_texto(x: float) -> str:
    return f"{x:g}"


def nota_checkin(texto: str, fecha: str, valores: dict, sensaciones: str) -> str:
    """Pone los valores en el frontmatter (sin quitar nada) y añade las sensaciones como viñetas."""
    nl = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.replace("\r\n", "\n").split("\n")
    if not lineas or lineas[0].strip() != "---":
        raise ValueError("la nota del check-in no tiene frontmatter")
    fin = next((i for i in range(1, len(lineas)) if lineas[i].strip() == "---"), None)
    if fin is None:
        raise ValueError("la nota del check-in no tiene frontmatter")
    claves = [k for k, *_ in CAMPOS]

    def pos(clave):
        for i in range(1, fin):
            if re.match(rf"{re.escape(clave)}\s*:", lineas[i]):
                return i
        return None

    for k in ("fecha", *claves):
        if k == "fecha":
            v = fecha
        elif k in valores:
            v = valor_texto(valores[k])
        else:
            continue
        i = pos(k)
        if i is not None:
            lineas[i] = f"{k}: {v}"
            continue
        # clave que falta: detrás de la anterior que exista (brazo_flex detrás de brazo), si no, al final
        previas = ["tipo", "fecha", *claves[:claves.index(k)]] if k != "fecha" else ["tipo"]
        detras = next((pos(p) for p in reversed(previas) if pos(p) is not None), None)
        donde = (detras + 1) if detras is not None else fin
        lineas.insert(donde, f"{k}: {v}")
        fin += 1
    sens = [s.strip() for s in sensaciones.replace("\r\n", "\n").split("\n") if s.strip()]
    if sens:
        vinetas = [s if s.startswith("- ") else "- " + s.lstrip("-• ").strip() for s in sens]
        ini = next((i for i in range(fin, len(lineas)) if lineas[i].strip().lower() == "## sensaciones"), None)
        if ini is None:
            lineas[fin + 1:fin + 1] = ["", "## Sensaciones", "", *vinetas]
        else:
            sig = next((i for i in range(ini + 1, len(lineas)) if lineas[i].startswith("## ")), len(lineas))
            cuerpo = lineas[ini + 1:sig]
            # la plantilla trae un texto de ayuda entre paréntesis: se sustituye
            cuerpo = [l for l in cuerpo if not (l.startswith("(") and l.rstrip().endswith(")"))]
            while cuerpo and not cuerpo[-1].strip():
                cuerpo.pop()
            while cuerpo and not cuerpo[0].strip():
                cuerpo.pop(0)
            nuevo = ["", *cuerpo, *vinetas, ""]
            lineas[ini + 1:sig] = nuevo
    return nl.join(lineas)


def guardar_checkin(d: dict):
    fecha = d.get("fecha")
    if not fecha_valida(fecha):
        return 400, {"ok": False, "mensaje": "Fecha no válida."}
    valores = {}
    for k, nombre, lo, hi in CAMPOS:
        x = (d.get("valores") or {}).get(k)
        if x in (None, ""):
            continue
        n = entreno.num(str(x))
        if n is None or not lo <= n <= hi:
            return 400, {"ok": False, "mensaje": f"{nombre}: valor fuera de rango ({lo}-{hi})."}
        valores[k] = round(n, 1)
    sensaciones = str(d.get("sensaciones") or "")[:3000]
    if not valores and not sensaciones.strip():
        return 400, {"ok": False, "mensaje": "No hay nada que guardar."}
    nota = CHECKINS / f"{fecha}.md"
    base = huella(nota)
    if str(d.get("base") or "") != base:
        return 409, {"ok": False, "mensaje": "El check-in ha cambiado en el vault. Recarga y vuelve a guardar."}
    try:
        texto = nota.read_text(encoding="utf-8") if base else PLANTILLA.read_text(encoding="utf-8").replace(
            "fotos: 40-fotos/AAAA-MM-DD/", "fotos:")
        texto = nota_checkin(texto, fecha, valores, sensaciones)
    except (OSError, ValueError) as e:
        return 500, {"ok": False, "mensaje": f"No se ha podido preparar la nota: {e}"}
    ok, error = pedir({"op": "checkin", "fecha": fecha, "base": base, "texto": texto})
    if not ok:
        return (409 if "cambiado" in error else 500), {"ok": False, "mensaje": error}
    return 200, {"ok": True, "nuevo": not base}


def subir_foto(query: str, cuerpo: bytes):
    q = parse_qs(query)
    fecha, pose = q.get("fecha", [""])[0], q.get("pose", [""])[0]
    if not fecha_valida(fecha) or pose not in dict(POSES):
        return 400, {"ok": False, "mensaje": "Fecha o pose no válida."}
    if not cuerpo.startswith(b"\xff\xd8\xff"):
        return 400, {"ok": False, "mensaje": "No es una imagen JPEG."}
    ok, error = pedir({"op": "foto", "fecha": fecha, "pose": pose}, cuerpo)
    return (200, {"ok": True}) if ok else (500, {"ok": False, "mensaje": error})


def operar_foto(d: dict):
    """mover: {fecha, pose, a_fecha, a_pose}; eliminar: {fecha, pose}."""
    op, fecha, pose = d.get("op"), d.get("fecha"), d.get("pose")
    if not fecha_valida(fecha) or pose not in dict(POSES):
        return 400, {"ok": False, "mensaje": "Fecha o pose no válida."}
    if not (FOTOS / fecha / f"{pose}.jpg").is_file():
        return 409, {"ok": False, "mensaje": "Esa foto ya no está. Recarga."}
    pet = {"op": op, "fecha": fecha, "pose": pose}
    if op == "mover":
        a_fecha, a_pose = d.get("a_fecha"), d.get("a_pose")
        if not fecha_valida(a_fecha) or a_pose not in dict(POSES):
            return 400, {"ok": False, "mensaje": "Fecha o pose de destino no válida."}
        if (a_fecha, a_pose) == (fecha, pose):
            return 200, {"ok": True}
        pet.update(a_fecha=a_fecha, a_pose=a_pose)
    elif op != "eliminar":
        return 400, {"ok": False, "mensaje": "Operación desconocida."}
    ok, error = pedir(pet)
    return (200, {"ok": True}) if ok else (500, {"ok": False, "mensaje": error})


# ---------- reparto de rutas (desde portal.py) ----------
def _json(h, codigo, d):
    datos = json.dumps(d, ensure_ascii=False).encode()
    h.send_response(codigo)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    h.send_header("Content-Length", str(len(datos)))
    h.cabeceras_comunes()
    h.end_headers()
    if h.command != "HEAD":
        h.wfile.write(datos)


def get(h, ruta: str) -> bool:
    """Rutas GET de Perímetros y Fotos. Se llama ya con la sesión comprobada. False = no es mía."""
    if ruta == "/entreno/medidas/datos":
        _json(h, 200, datos(h))
        return True
    m = re.fullmatch(r"/entreno/fotos/(\d{4}-\d{2}-\d{2})/([a-z-]+)\.jpg", ruta)
    if m:
        f = FOTOS / m.group(1) / f"{m.group(2)}.jpg"
        if privado(h) or m.group(2) not in dict(POSES) or not f.is_file():
            h.enviar("No encontrado", 404)
            return True
        cuerpo = f.read_bytes()
        h.send_response(200)
        h.send_header("Content-Type", "image/jpeg")
        h.send_header("Content-Length", str(len(cuerpo)))
        h.send_header("Cache-Control", "private, max-age=604800")  # la URL lleva ?v=<mtime>
        h.send_header("X-Content-Type-Options", "nosniff")
        h.end_headers()
        if h.command != "HEAD":
            h.wfile.write(cuerpo)
        return True
    return False


def post(h, ruta: str, sesion_ok) -> bool:
    """Rutas POST. sesion_ok() comprueba la sesión (solo se llama si la ruta es mía). False = no es mía."""
    if ruta not in ("/entreno/medidas/checkin", "/entreno/fotos/subir", "/entreno/fotos/mover", "/entreno/fotos/eliminar"):
        return False
    if not sesion_ok():
        _json(h, 401, {"ok": False, "mensaje": "Sesión caducada. Vuelve a entrar."})
        return True
    largo = int(h.headers.get("Content-Length") or 0)
    if ruta.startswith("/entreno/fotos/") and privado(h):
        _json(h, 403, {"ok": False, "mensaje": "En modo privado no se tocan las fotos."})
        return True
    if ruta == "/entreno/fotos/subir":
        if largo > MAX_FOTO:
            _json(h, 413, {"ok": False, "mensaje": "La foto pesa demasiado (máx. 4 MB)."})
            return True
        codigo, r = subir_foto(urlsplit(h.path).query, h.rfile.read(largo))
    else:
        try:
            d = json.loads(h.rfile.read(min(largo, 16384)))
            if not isinstance(d, dict):
                raise ValueError
        except ValueError:
            _json(h, 400, {"ok": False, "mensaje": "Petición no válida."})
            return True
        if ruta == "/entreno/medidas/checkin":
            codigo, r = guardar_checkin(d)
        else:
            codigo, r = operar_foto({**d, "op": ruta.rsplit("/", 1)[1]})
    _json(h, codigo, r)
    return True
