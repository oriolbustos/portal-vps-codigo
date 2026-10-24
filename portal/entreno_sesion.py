#!/usr/bin/env python3
"""Entreno en directo (/entreno/sesion/): hacer la sesión en el gimnasio y apuntarla serie a serie.

Lee (con las funciones de entreno.py) qué toca por la rotación push → pierna → pull, los ejercicios
propuestos de la tabla "Estado actual" de 30 - Health/progress/<tipo>.md (en su "Orden fijo", con
"Siguiente paso" y "Descanso") y lo apuntado hoy en la tabla "Entreno" de la nota diaria.

Escribir: el portal no puede tocar el vault. Cada serie apuntada reescribe la tabla "Entreno" de la nota
de hoy entera (la página es dueña de esa tabla) y deja la nota completa en
/var/lib/portal/entreno-sesion-pendiente/; entreno-sesion-guardar.path → entreno-sesion-guardar.py (root)
la escribe solo si la nota no ha cambiado desde que se leyó (base = sha256) o, si no existía, la crea
desde la plantilla _system/templates/diario.md.

Formato de la tabla (30 - Health/_system/mod-log-strength-session.md):
  | Ejercicio | Serie | Reps | Peso (kg) | RIR | Agarre / altura | Notas | Descanso |
Descanso = lo descansado después de esa serie (m:ss), si se guardó con el temporizador.
Las notas antiguas, sin columnas RIR ni Agarre, llevan el RIR en Notas ("RIR 2"): al reescribir la tabla
se pasa a su columna; el resto de Notas se conserva tal cual.
"""
import hashlib, json, os, re, secrets, time
from datetime import date, timedelta
from pathlib import Path

import entreno
import rutas

PLANTILLA = rutas.PLANTILLA_DIARIO
MUSCULOS = rutas.MUSCULOS   # qué trabaja cada ejercicio (lo edita Oriol)
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "entreno-sesion-pendiente"
RESULTADO = ESTADO / "entreno-sesion-resultado.json"
CABECERA = ["Ejercicio", "Serie", "Reps", "Peso (kg)", "RIR", "Agarre / altura", "Notas", "Descanso"]
# Descanso por defecto si "Estado actual" no trae columna Descanso: multiarticulares 2:30, el resto 1:30.
MULTI = re.compile(r"dominada|jalon|remo|press|dips|fondos|prensa|peso muerto|sentadilla|hip thrust")
SOLO_RIR = re.compile(r"^RIR\s*(\d+(?:[.,]\d)?)$", re.I)


def musculos() -> dict:
    """Tabla de reference/musculos.md → {plano(ejercicio): {pri, sec, notar}}."""
    try:
        texto = MUSCULOS.read_text(encoding="utf-8")
    except OSError:
        return {}
    out = {}
    lista = lambda t: [m.strip().lower() for m in t.split(",") if m.strip()]
    for linea in texto.splitlines():
        c = [x.strip() for x in linea.strip().strip("|").split("|")]
        if not linea.lstrip().startswith("|") or len(c) < 4 or c[0].lower() == "ejercicio" or set(c[0]) <= set("-: "):
            continue
        out[entreno.plano(entreno.sin_md(c[0]))] = {"pri": lista(c[1]), "sec": lista(c[2]), "notar": entreno.sin_md(c[3])}
    return out


class Error(Exception):
    pass


def segundos(t: str):
    """'2:30' → 150, '90 s' → 90, '2 min' → 120, '' → None."""
    t = (t or "").strip().lower()
    m = re.match(r"(\d+)\s*[:']\s*(\d{1,2})", t)
    if m:
        return int(m[1]) * 60 + int(m[2])
    m = re.match(r"(\d+(?:[.,]\d+)?)\s*(min|m\b)", t)
    if m:
        return round(float(m[1].replace(",", ".")) * 60)
    m = re.match(r"(\d+)\s*(s|seg)?\b", t)
    if m:
        n = int(m[1])
        return n * 60 if n <= 5 else n  # "3" = 3 min
    return None


def descanso_defecto(nombre: str) -> int:
    return 150 if MULTI.search(entreno.plano(nombre)) else 90


def modo_peso(kg_txt: str, asistido: bool) -> str:
    """Cómo se apunta el peso de ese ejercicio: kg, mano (12.5/mano), ayuda (27 ayuda) o corporal."""
    p = entreno.plano(kg_txt or "")
    if asistido or "ayuda" in p or "asist" in p:
        return "ayuda"
    if "mano" in p:
        return "mano"
    if "corporal" in p:
        return "corporal"
    return "kg"


# ---------- nota del día ----------

def ruta_dia(f: str) -> Path:
    return entreno.DIARIO / f[:4] / f[5:7] / f"{f}.md"


def bloque_tabla(lineas: list):
    """→ (i_titulo, i_ini, i_fin) de la tabla bajo "## Entreno"; i_ini = i_fin si no hay tabla; None si no hay sección."""
    t = next((i for i, l in enumerate(lineas) if re.match(r"^##\s+entreno\s*$", entreno.plano(l.strip()))), None)
    if t is None:
        return None
    i = t + 1
    while i < len(lineas) and not lineas[i].strip():
        i += 1
    if i < len(lineas) and lineas[i].lstrip().startswith("|"):
        j = i
        while j < len(lineas) and lineas[j].lstrip().startswith("|"):
            j += 1
        return t, i, j
    return t, t + 1, t + 1


def celdas(linea: str) -> list:
    return [c.strip() for c in linea.strip().strip("|").split("|")]


def leer_tabla(texto: str):
    """→ (huella de la tabla, filas como dict). Huella = sha256 de sus líneas (cambia si alguien la toca)."""
    lineas = texto.splitlines()
    b = bloque_tabla(lineas)
    if not b or b[1] == b[2]:
        return hashlib.sha256(b"").hexdigest(), []
    crudo = [l.rstrip() for l in lineas[b[1]:b[2]]]
    cab = [entreno.plano(c) for c in celdas(crudo[0])]
    filas = []
    for l in crudo[1:]:
        cs = celdas(l)
        if all(re.fullmatch(r":?-*:?", c) for c in cs) or not any(cs):
            continue
        fila = {cab[i] if i < len(cab) else f"c{i}": c for i, c in enumerate(cs)}
        notas, rir = entreno.col(fila, "notas"), entreno.col(fila, "rir")
        if not any(k.startswith("rir") for k in cab):  # formato antiguo: "RIR 2" dentro de Notas
            resto = []
            for trozo in [x.strip() for x in notas.split(",")]:
                m = SOLO_RIR.match(trozo)
                if m and not rir:
                    rir = m[1]
                elif trozo:
                    resto.append(trozo)
            notas = ", ".join(resto)
        filas.append({"ej": entreno.col(fila, "ejercicio"), "reps": entreno.col(fila, "reps"),
                      "peso": entreno.col(fila, "peso"), "rir": rir, "agarre": entreno.col(fila, "agarre"), "notas": notas,
                      "descanso": entreno.col(fila, "descanso")})
    return hashlib.sha256("\n".join(crudo).encode()).hexdigest(), filas


def limpia(t) -> str:
    return re.sub(r"\s+", " ", str(t if t is not None else "")).replace("|", "/").strip()[:200]


def tabla_md(filas: list) -> list:
    out = ["| " + " | ".join(CABECERA) + " |", "|" + "|".join("-" * (len(c) + 2) for c in CABECERA) + "|"]
    n = {}
    for f in filas:
        ej = limpia(f.get("ej"))
        if not ej:
            continue
        n[ej] = n.get(ej, 0) + 1
        out.append("| " + " | ".join([ej, str(n[ej]), limpia(f.get("reps")), limpia(f.get("peso")), limpia(f.get("rir")),
                                      limpia(f.get("agarre")), limpia(f.get("notas")), limpia(f.get("descanso"))]) + " |")
    return out


def con_tabla(texto: str, filas: list) -> str:
    nl = "\r\n" if "\r\n" in texto else "\n"
    final = texto.endswith(("\n", "\r\n"))
    lineas = texto.splitlines()
    nueva = tabla_md(filas)
    b = bloque_tabla(lineas)
    if b is None:  # sin sección: antes de "Deporte y gasto" o al final
        d = next((i for i, l in enumerate(lineas) if entreno.plano(l.strip()).startswith("## deporte")), len(lineas))
        lineas[d:d] = ["## Entreno", "", *nueva, ""]
    elif b[1] == b[2]:
        lineas[b[0] + 1:b[0] + 1] = ["", *nueva]
    else:
        lineas[b[1]:b[2]] = nueva
    return nl.join(lineas) + (nl if final else "")


def desde_plantilla(f: str) -> str:
    texto = PLANTILLA.read_text(encoding="utf-8")
    return re.sub(r"(?m)^fecha:[ \t]*$", f"fecha: {f}", texto, count=1)


def fechas_validas():
    hoy = date.today()
    return {hoy.isoformat(), (hoy - timedelta(days=1)).isoformat()}  # ayer: sesión que pasa de medianoche


# ---------- datos para la página ----------

def datos(f: str = None) -> dict:
    f = f or date.today().isoformat()
    dias = entreno.leer_todo()
    ejercicios = entreno.resumen_ejercicios(dias)
    estado = entreno.estado_sesiones()

    # qué toca: si hoy ya hay series, el tipo de hoy; si no, el siguiente de la rotación (30 - Health/_context.md, Current state)
    hoy_d = next((d for d in dias if d["fecha"] == f), None)
    previas = [d for d in dias if d["fecha"] < f and d.get("sesiones")]
    ultima = previas[-1] if previas else None
    if hoy_d and hoy_d.get("sesiones"):
        toca = next((t for t in entreno.TIPOS if t in hoy_d["sesiones"]), "push")
    else:
        t_ult = next((t for t in entreno.TIPOS if ultima and t in ultima["sesiones"]), None)
        toca = entreno.TIPOS[(entreno.TIPOS.index(t_ult) + 1) % 3] if t_ult else "push"

    def info(e: dict) -> dict:
        pts = e["puntos"]
        ult = next((p for p in reversed(pts) if p["fecha"] < f), None)
        kg_txt = ult["series"][0][1] if ult and ult["series"] else ""
        return {"ej": e["ej"], "sesion": e["sesion"], "asistido": e["asistido"], "modo": modo_peso(kg_txt, e["asistido"]),
                "ultima": {"fecha": ult["fecha"], "series": ult["series"], "rir_min": ult["rir_min"]} if ult else None,
                "tendencia": [[p["fecha"], p["kg"], p["reps"], p["seg"]] for p in pts if p["fecha"] < f][-3:]}

    por_ej = {e: info(v) for e, v in ejercicios.items()}

    # catálogo para añadir ejercicios: todos los nombres del histórico y de "Estado actual"
    catalogo = {}
    for e in ejercicios.values():
        for n in e["nombres"]:
            catalogo[n] = {"ej": e["ej"], "descanso": descanso_defecto(n)}
    tipos = {}
    for tipo in entreno.TIPOS:
        E = estado.get(tipo, {"estado": [], "notas": [], "avisos": [], "ultima": None})
        try:
            sec = entreno.secciones((entreno.HISTORICO / f"{tipo}.md").read_text(encoding="utf-8"))
            filas_est = next((v for k, v in sec.items() if k.startswith("estado actual")), [])
        except OSError:
            filas_est = []
        descansos = {entreno.sin_md(entreno.col(r, "ejercicio")): segundos(entreno.sin_md(entreno.col(r, "descanso"))) for r in filas_est}
        orden_txt = next((n for n in E["notas"] if re.match(r"orden fijo", entreno.plano(n))), "")
        orden = [entreno.plano(x.strip().rstrip(".")) for x in orden_txt.split(":", 1)[-1].split("→")] if orden_txt else []

        def pos(r):
            return next((i for i, o in enumerate(orden) if o and (o in entreno.plano(r["nombre"]) or o in entreno.plano(r["ej"]))), 99)

        filas = sorted(enumerate(E["estado"]), key=lambda x: (pos(x[1]), x[0]))
        lista, vistos = [], set()
        for _, r in filas:
            vistos.add(r["ej"])
            d = descansos.get(r["nombre"]) or descanso_defecto(r["nombre"])
            lista.append({"nombre": r["nombre"], "ej": r["ej"], "siguiente": r["siguiente"], "carga": r["carga"], "descanso": d})
            catalogo[r["nombre"]] = {"ej": r["ej"], "descanso": d, "siguiente": r["siguiente"]}
        ult_tipo = next((d for d in reversed(previas) if tipo in d["sesiones"]), None)
        if ult_tipo:  # lo hecho la última vez que no está en "Estado actual"
            for s in ult_tipo["series"]:
                if s["sesion"] == tipo and s["ej"] not in vistos:
                    vistos.add(s["ej"])
                    lista.append({"nombre": s["orig"], "ej": s["ej"], "siguiente": "", "carga": "", "descanso": descanso_defecto(s["orig"])})
        tipos[tipo] = {"ejercicios": lista, "ultima": ult_tipo["fecha"] if ult_tipo else None,
                       "notas": [n for n in E["notas"] if n != orden_txt], "orden": orden_txt.split(":", 1)[-1].strip() if orden_txt else "",
                       "avisos": E["avisos"]}

    ruta = ruta_dia(f)
    existe = ruta.is_file()
    huella, filas_hoy = leer_tabla(ruta.read_text(encoding="utf-8")) if existe else (hashlib.sha256(b"").hexdigest(), [])
    return {"fecha": f, "toca": toca, "ultima": {"fecha": ultima["fecha"], "sesiones": ultima["sesiones"]} if ultima else None,
            "tipos": tipos, "ejercicios": por_ej, "catalogo": catalogo, "musculos": musculos(),
            "hoy": {"existe": existe, "huella": huella, "filas": filas_hoy}}


# ---------- guardar ----------

def guardar_nota(f: str, base: str, texto: str, crear: bool):
    """Deja la nota para entreno-sesion-guardar y espera su respuesta → (ok, error)."""
    pid = secrets.token_hex(8)
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"entreno-sesion-{pid}.tmp"
    tmp.write_text(json.dumps({"id": pid, "fecha": f, "base": base, "crear": crear, "texto": texto}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(50):  # el .path lo recoge en menos de un segundo
        time.sleep(0.1)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return r.get("ok"), r.get("error", "")
    return False, "El guardado no responde. Revisa entreno-sesion-guardar en el servidor."


def operar(d: dict):
    """d = {fecha, huella, filas: [{ej, reps, peso, rir, agarre, notas, descanso}]} → reescribe la tabla Entreno de ese día.
    huella = la de la tabla que la página vio la última vez: si alguien la ha cambiado, 409 con lo que hay ahora."""
    if not isinstance(d, dict) or not isinstance(d.get("filas"), list) or len(d["filas"]) > 300:
        return 400, {"ok": False, "mensaje": "Petición no válida."}
    f = str(d.get("fecha", ""))
    if f not in fechas_validas():
        return 400, {"ok": False, "mensaje": f"Solo se apunta en la nota de hoy o de ayer, no en {f}."}
    filas = [x for x in d["filas"] if isinstance(x, dict)]
    ruta = ruta_dia(f)
    try:
        existe = ruta.is_file()
        actual = ruta.read_text(encoding="utf-8") if existe else ""
    except OSError as e:
        return 500, {"ok": False, "mensaje": f"No puedo leer la nota: {e}"}
    huella, filas_hoy = leer_tabla(actual) if existe else (hashlib.sha256(b"").hexdigest(), [])
    if huella != d.get("huella"):
        return 409, {"ok": False, "mensaje": "La tabla de entreno de hoy ha cambiado fuera de esta página.",
                     "hoy": {"existe": existe, "huella": huella, "filas": filas_hoy}}
    try:
        texto = con_tabla(actual if existe else desde_plantilla(f), filas)
    except OSError as e:
        return 500, {"ok": False, "mensaje": f"No puedo leer la plantilla: {e}"}
    base = hashlib.sha256(ruta.read_bytes()).hexdigest() if existe else ""  # el escritor compara los bytes
    ok, error = guardar_nota(f, base, texto, not existe)
    if not ok:
        return 409, {"ok": False, "mensaje": error or "No se ha guardado."}
    huella, filas_hoy = leer_tabla(texto)
    return 200, {"ok": True, "hoy": {"existe": True, "huella": huella, "filas": filas_hoy}}


def html(plantilla: Path) -> str:
    js = json.dumps(datos(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)


if __name__ == "__main__":
    import sys
    print(json.dumps(datos(sys.argv[1] if len(sys.argv) > 1 else None), ensure_ascii=False, indent=1))
