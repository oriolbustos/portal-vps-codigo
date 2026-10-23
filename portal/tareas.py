#!/usr/bin/env python3
"""Panel Tareas: las tareas de cada ámbito del vault, con su matriz de Eisenhower. Se leen y se editan aquí.

Fuente: "40 - Life/Panel.md" da los ámbitos ([[Identity]], [[Work]]...); de cada nota de
ámbito se usa la sección "## Plan": "### Doing", "### To Do" y "### Monitoring", una tarea por viñeta.
Formato de una tarea (se puede escribir a mano en Obsidian):
  - Certi AWS [imp:: 4] [urg:: 2] [esf:: 3]   importancia, urgencia y esfuerzo de 0 a 5 (sin ellas = sin puntuar)
  	- paso                              subtareas: viñetas con sangría (o "- - paso", formato antiguo)
  	> nota                              notas: líneas con sangría que empiezan por "> "
  ## Done                               "Marcar hecha" la mueve aquí, arriba del todo: "- [x] ... ✅ AAAA-MM-DD"
  ## Eliminadas                         lo que se borra desde el portal acaba aquí, al final de la nota,
                                        con "(eliminada AAAA-MM-DD)". No se ve en el panel y nunca se borra.
Las etiquetas #urgente y #importante, si una tarea no tiene puntuación, cuentan como 4.

Escribir: el portal no puede tocar el vault. Cada cambio se aplica aquí sobre la nota entera y se deja en
/var/lib/portal/tareas-pendiente/; tareas-guardar.path → /opt/portal/tareas-guardar.py (root) la escribe
solo si la nota no ha cambiado desde que se leyó (base = sha256).
"""
import hashlib, json, os, re, secrets, time
from pathlib import Path

import rutas

PLAN = rutas.VIDA
PANEL = rutas.PANEL
AMBITOS = rutas.AMBITOS
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "tareas-pendiente"
RESULTADO = ESTADO / "tareas-resultado.json"
ESTADOS = {"doing": "doing", "to do": "todo", "todo": "todo", "monitoring": "seguimiento", "monitorizando": "seguimiento"}
TITULOS = {"doing": "Doing", "todo": "To Do", "seguimiento": "Monitoring"}
CAMPO = re.compile(r"\s*\[(imp|urg|esf)::\s*([0-5])\s*\]", re.I)
TITULO = re.compile(r"^#{1,6}\s")
CASILLA = re.compile(r"^\[[ /-]\]\s*")
ELIMINADAS = "Eliminadas"
HECHAS = "Done"


class Error(Exception):
    pass


def ambitos() -> list:
    """[(nombre, ruta)] en el orden de Panel.md."""
    try:
        texto = PANEL.read_text(encoding="utf-8")
    except OSError:
        return []
    nombres = []
    for n in re.findall(r"\[\[([^\]|#]+)", texto):
        n = n.strip()
        if n not in nombres:
            nombres.append(n)
    out = []
    for n in nombres:
        ruta = next(iter(sorted(p for p in AMBITOS.rglob(f"{n}.md") if not any(s.startswith("_") for s in p.relative_to(AMBITOS).parts))), None)
        if ruta:
            out.append((n, ruta))
    return out


def ruta_de(ambito: str) -> Path:
    r = dict(ambitos()).get(ambito)
    if not r:
        raise Error("Ese ámbito ya no está en Panel.md.")
    return r


def legible(t: str) -> str:
    """Texto para enseñar: sin [[ ]] de los enlaces."""
    return re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", t).strip()


def huella(ambito: str, texto: str) -> str:
    return hashlib.sha1(f"{ambito}|{texto.lower().strip()}".encode()).hexdigest()[:12]


def _leer(ruta: Path):
    crudo = ruta.read_bytes()
    texto = crudo.decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.split(nl)
    final = len(lineas) > 1 and lineas[-1] == ""
    if final:
        lineas.pop()
    return lineas, nl, final, hashlib.sha256(crudo).hexdigest()


def _hija(linea: str):
    """Línea que cuelga de una tarea → ("sub"|"nota", texto) o None."""
    if not linea.strip():
        return None
    m = re.match(r"^-\s+-\s*(.*)$", linea)  # "- - paso"
    if m:
        return ("sub", m.group(1).strip())
    if not linea[0].isspace():
        return None
    s = linea.strip()
    if s.startswith(">"):
        return ("nota", s[1:].strip())
    m = re.match(r"^-\s+(?:\[[ xX/-]\]\s*)?(.*)$", s)
    if m:
        return ("sub", m.group(1).strip())
    return ("nota", s)


def analizar(nombre: str, lineas: list) -> list:
    """Tareas de la sección Plan, con la posición de su bloque en la nota (n = primera línea, fin = tras la última)."""
    tareas, estado, en_plan, actual, vistas = [], None, False, None, {}
    for n, linea in enumerate(lineas):
        if actual is not None:
            h = _hija(linea)
            if h:
                actual["fin"] = n + 1
                if h[1]:
                    actual["sub" if h[0] == "sub" else "notas"].append(h[1])
                continue
            actual = None
        if TITULO.match(linea):
            nivel = len(linea) - len(linea.lstrip("#"))
            if nivel <= 2:
                en_plan = nivel == 2 and linea[3:].strip().lower() == "plan"
                estado = None
            elif en_plan and nivel == 3:
                estado = ESTADOS.get(linea[4:].strip().lower())
            continue
        if not en_plan or not estado:
            continue
        m = re.match(r"^-\s+(.*)$", linea)
        if not m:
            continue
        cuerpo = m.group(1)
        if cuerpo.startswith("-"):  # subtarea suelta, sin tarea encima
            continue
        if re.match(r"^\[[xX]\]", cuerpo):  # hecha: se salta con sus hijas
            actual = {"sub": [], "notas": [], "fin": n + 1}
            continue
        casilla = CASILLA.match(cuerpo)
        crudo = cuerpo[casilla.end():] if casilla else cuerpo
        campos = {k.lower(): int(v) for k, v in CAMPO.findall(crudo)}
        crudo = CAMPO.sub("", crudo).strip()
        if not crudo:
            continue
        tag_u = bool(re.search(r"#urgente\b", crudo, re.I))
        tag_i = bool(re.search(r"#importante\b", crudo, re.I))
        visible = legible(re.sub(r"\s*#(urgente|importante)\b", "", crudo, flags=re.I))
        clave = huella(nombre, crudo)
        vistas[clave] = vistas.get(clave, 0) + 1
        actual = {"id": clave if vistas[clave] == 1 else f"{clave}-{vistas[clave]}", "texto": visible, "crudo": crudo,
                  "ambito": nombre, "estado": estado, "sub": [], "notas": [],
                  "imp": campos.get("imp", 4 if tag_i else None), "urg": campos.get("urg", 4 if tag_u else None),
                  "esf": campos.get("esf"),
                  "n": n, "fin": n + 1, "casilla": casilla.group(0) if casilla else ""}
        tareas.append(actual)
    return tareas


FECHA_HECHA = re.compile(r"\s*✅\s*(\d{4}-\d{2}-\d{2})")


def analizar_hechas(nombre: str, lineas: list) -> list:
    """Tareas de "## Done" (más reciente arriba): "- [x] Texto [imp:: 4] ✅ AAAA-MM-DD" o una viñeta suelta, con sus hijas."""
    hechas, dentro, actual, vistas = [], False, None, {}
    for n, linea in enumerate(lineas):
        if actual is not None:
            h = _hija(linea)
            if h:
                actual["fin"] = n + 1
                if h[1]:
                    actual["sub" if h[0] == "sub" else "notas"].append(h[1])
                continue
            actual = None
        if TITULO.match(linea):
            nivel = len(linea) - len(linea.lstrip("#"))
            if nivel <= 2:
                dentro = nivel == 2 and linea[3:].strip().lower() == HECHAS.lower()
            continue
        m = re.match(r"^-\s+(.*)$", linea) if dentro else None
        if not m or m.group(1).startswith("-"):
            continue
        crudo = re.sub(r"^\[[ xX/-]\]\s*", "", m.group(1))
        f = FECHA_HECHA.search(crudo)
        campos = {k.lower(): int(v) for k, v in CAMPO.findall(crudo)}
        texto = legible(CAMPO.sub("", FECHA_HECHA.sub("", crudo)))
        if not texto:
            continue
        clave = "h" + huella(nombre, texto)
        vistas[clave] = vistas.get(clave, 0) + 1
        actual = {"id": clave if vistas[clave] == 1 else f"{clave}-{vistas[clave]}", "texto": texto, "ambito": nombre,
                  "fecha": f.group(1) if f else None, "imp": campos.get("imp"), "urg": campos.get("urg"), "esf": campos.get("esf"),
                  "sub": [], "notas": [], "n": n, "fin": n + 1}
        hechas.append(actual)
    return hechas


def datos() -> dict:
    lista, hechas, avisos, nombres, bases = [], [], [], [], {}
    for nombre, ruta in ambitos():
        nombres.append(nombre)
        try:
            lineas, _, _, base = _leer(ruta)
            bases[nombre] = base
            lista += [{k: v for k, v in t.items() if k not in ("n", "fin", "casilla")} for t in analizar(nombre, lineas)]
            hechas += [{k: v for k, v in t.items() if k not in ("n", "fin")} for t in analizar_hechas(nombre, lineas)]
        except Exception as e:  # una nota rota no tumba el panel
            avisos.append(f"{nombre}: {e}")
    return {"ambitos": nombres, "tareas": lista, "hechas": hechas, "bases": bases, "avisos": avisos, "generado": time.strftime("%Y-%m-%d %H:%M")}


# ---------- escribir ----------

def _limpio(v, largo=500) -> str:
    if not isinstance(v, str):
        raise Error("Petición no válida.")
    v = re.sub(r"\s+", " ", v).strip()
    if len(v) > largo:
        raise Error("Texto demasiado largo.")
    return v


def _nota(v) -> list:
    if not isinstance(v, str) or len(v) > 5000:
        raise Error("Nota no válida.")
    lineas = [l.rstrip() for l in v.replace("\r\n", "\n").split("\n")]
    while lineas and not lineas[-1]:
        lineas.pop()
    while lineas and not lineas[0]:
        lineas.pop(0)
    return lineas


def _punto(v):
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 5:
        raise Error("La puntuación va de 0 a 5.")
    return v


def _linea(casilla: str, crudo: str, imp, urg, esf=None) -> str:
    campos = "".join(f" [{k}:: {v}]" for k, v in (("imp", imp), ("urg", urg), ("esf", esf)) if v is not None)
    if imp is not None or urg is not None:  # la puntuación sustituye a las etiquetas
        crudo = re.sub(r"\s*#(urgente|importante)\b", "", crudo, flags=re.I).strip()
    return f"- {casilla}{crudo}{campos}"


def _bloque(t: dict, crudo, imp, urg, esf, sub, notas, original: list) -> list:
    hijas = original[1:] if (sub == t["sub"] and notas == t["notas"]) else \
        [f"\t- {s}" for s in sub] + [f"\t> {x}" if x else "\t>" for x in notas]
    return [_linea(t["casilla"], crudo, imp, urg, esf)] + hijas


def _seccion(lineas: list, estado: str) -> int:
    """Posición donde añadir una tarea al final de "### <estado>" de "## Plan" (la crea si no está)."""
    p = next((i for i, l in enumerate(lineas) if re.match(r"^##\s+plan\s*$", l, re.I)), None)
    if p is None:
        lineas[:0] = ["## Plan", f"### {TITULOS[estado]}", ""]
        return 2
    fin_plan = next((i for i in range(p + 1, len(lineas)) if re.match(r"^#{1,2}\s", lineas[i])), len(lineas))
    s = next((i for i in range(p + 1, fin_plan) if lineas[i].startswith("### ") and ESTADOS.get(lineas[i][4:].strip().lower()) == estado), None)
    if s is None:
        pos = fin_plan
        while pos - 1 > p and not lineas[pos - 1].strip():
            pos -= 1
        lineas[pos:pos] = [f"### {TITULOS[estado]}"]
        return pos + 1
    fin = next((i for i in range(s + 1, fin_plan) if TITULO.match(lineas[i])), fin_plan)
    while fin - 1 > s and not lineas[fin - 1].strip():
        fin -= 1
    return fin


def _a_eliminadas(lineas: list, bloque: list):
    bloque = [bloque[0] + f" (eliminada {time.strftime('%Y-%m-%d')})"] + bloque[1:]
    e = next((i for i, l in enumerate(lineas) if re.match(rf"^##\s+{ELIMINADAS}\s*$", l)), None)
    if e is None:
        while lineas and not lineas[-1].strip():
            lineas.pop()
        lineas += ["", f"## {ELIMINADAS}"]
        e = len(lineas) - 1
    lineas[e + 1:e + 1] = bloque


def _a_done(lineas: list, bloque: list):
    """Tarea hecha: "- [x] ... ✅ AAAA-MM-DD" arriba del todo de "## Done" (la crea antes de Eliminadas o al final)."""
    bloque = [re.sub(r"^- (?:\[[ /-]\] ?)?", "- [x] ", bloque[0]) + f" ✅ {time.strftime('%Y-%m-%d')}"] + bloque[1:]
    h = next((i for i, l in enumerate(lineas) if re.match(rf"^##\s+{HECHAS}\s*$", l, re.I)), None)
    if h is None:
        e = next((i for i, l in enumerate(lineas) if re.match(rf"^##\s+{ELIMINADAS}\s*$", l)), None)
        if e is None:
            while lineas and not lineas[-1].strip():
                lineas.pop()
            e = len(lineas)
        lineas[e:e] = ["", f"## {HECHAS}"] if e and lineas[e - 1].strip() else [f"## {HECHAS}"]
        h = next(i for i, l in enumerate(lineas) if l == f"## {HECHAS}")
    lineas[h + 1:h + 1] = bloque


def guardar(ruta: Path, base: str, texto: str):
    """Deja la nota editada para tareas-guardar y espera su respuesta → (ok, error)."""
    pid = secrets.token_hex(8)
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"tareas-{pid}.tmp"
    tmp.write_text(json.dumps({"id": pid, "ruta": str(ruta.relative_to(AMBITOS)), "base": base, "texto": texto}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(50):  # tareas-guardar.path lo recoge en menos de un segundo
        time.sleep(0.1)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return r.get("ok"), r.get("error", "")
    return False, "El guardado no responde. Revisa tareas-guardar en el servidor."


def _escribir(ruta, base, lineas, nl, final):
    ok, error = guardar(ruta, base, nl.join(lineas) + (nl if final else ""))
    if not ok:
        raise Error(error or "No se ha guardado.")


def _id_en(nombre: str, lineas: list, n: int):
    return next((t["id"] for t in analizar(nombre, lineas) if t["n"] == n), None)


def aplicar(d: dict):
    """Aplica la operación en el vault → id de la tarea resultante (o None si se eliminó)."""
    op, ambito = d.get("op"), d.get("ambito")
    ruta = ruta_de(ambito if isinstance(ambito, str) else "")
    lineas, nl, final, base = _leer(ruta)
    if d.get("base") != base:
        raise Error("La nota de este ámbito ha cambiado; la he recargado.")

    if op == "crear":
        estado = d.get("estado") or "todo"
        if estado not in TITULOS:
            raise Error("Estado no válido.")
        texto = _limpio(d.get("texto"))
        if not texto:
            raise Error("Escribe la tarea.")
        pos = _seccion(lineas, estado)
        lineas[pos:pos] = [_linea("", texto, _punto(d.get("imp")), _punto(d.get("urg")), _punto(d.get("esf")))]
        _escribir(ruta, base, lineas, nl, final)
        return _id_en(ambito, lineas, pos)

    if op == "reabrir":  # una hecha vuelve al final de "### To Do", sin la casilla ni la fecha de hecha
        h = next((x for x in analizar_hechas(ambito, lineas) if x["id"] == d.get("id")), None)
        if not h:
            raise Error("No encuentro esa tarea en Done; la he recargado.")
        bloque = lineas[h["n"]:h["fin"]]
        bloque[0] = FECHA_HECHA.sub("", re.sub(r"^-\s+\[[ xX/-]\]\s*", "- ", bloque[0])).rstrip()
        del lineas[h["n"]:h["fin"]]
        pos = _seccion(lineas, "todo")
        lineas[pos:pos] = bloque
        _escribir(ruta, base, lineas, nl, final)
        return _id_en(ambito, lineas, pos)

    t = next((x for x in analizar(ambito, lineas) if x["id"] == d.get("id")), None)
    if not t:
        raise Error("No encuentro esa tarea en la nota; la he recargado.")
    original = lineas[t["n"]:t["fin"]]

    if op in ("borrar", "completar"):
        del lineas[t["n"]:t["fin"]]
        (_a_eliminadas if op == "borrar" else _a_done)(lineas, original)
        _escribir(ruta, base, lineas, nl, final)
        return None

    if op != "editar":
        raise Error("Operación desconocida.")
    crudo = _limpio(d["texto"]) if "texto" in d else t["crudo"]
    if not crudo:
        raise Error("La tarea no puede quedar vacía. Para quitarla, elimínala.")
    imp = _punto(d["imp"]) if "imp" in d else t["imp"]
    urg = _punto(d["urg"]) if "urg" in d else t["urg"]
    esf = _punto(d["esf"]) if "esf" in d else t["esf"]
    if "sub" in d:
        if not isinstance(d["sub"], list) or len(d["sub"]) > 100:
            raise Error("Subtareas no válidas.")
        sub = [s for s in (_limpio(x) for x in d["sub"]) if s]
    else:
        sub = t["sub"]
    notas = _nota(d["notas"]) if "notas" in d else t["notas"]
    estado = d.get("estado") or t["estado"]
    if estado not in TITULOS:
        raise Error("Estado no válido.")
    destino = d.get("ambito_nuevo") or ambito
    bloque = _bloque(t, crudo, imp, urg, esf, sub, notas, original)

    if destino != ambito:  # a otra nota: primero se añade allí, luego se quita de aquí (si falla, queda repetida, no perdida)
        ruta2 = ruta_de(destino)
        l2, nl2, final2, base2 = _leer(ruta2)
        pos = _seccion(l2, estado)
        l2[pos:pos] = bloque
        _escribir(ruta2, base2, l2, nl2, final2)
        del lineas[t["n"]:t["fin"]]
        _escribir(ruta, base, lineas, nl, final)
        return _id_en(destino, l2, pos)
    if estado != t["estado"]:
        del lineas[t["n"]:t["fin"]]
        pos = _seccion(lineas, estado)
        lineas[pos:pos] = bloque
    elif bloque == original:
        return t["id"]
    else:
        pos = t["n"]
        lineas[t["n"]:t["fin"]] = bloque
    _escribir(ruta, base, lineas, nl, final)
    return _id_en(ambito, lineas, pos)


def operar(d: dict):
    """d = {op: crear|editar|completar|reabrir|borrar, ambito, base, id, ...} → (código, respuesta con los datos nuevos)."""
    if not isinstance(d, dict):
        return 400, {"ok": False, "mensaje": "Petición no válida."}
    try:
        nuevo = aplicar(d)
    except Error as e:
        return 409, {"ok": False, "mensaje": str(e), "datos": datos()}
    except OSError as e:
        return 500, {"ok": False, "mensaje": f"No puedo leer la nota: {e}"}
    return 200, {"ok": True, "id": nuevo, "datos": datos()}


def html(plantilla: Path) -> str:
    js = json.dumps(datos(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)


if __name__ == "__main__":
    print(json.dumps(datos(), ensure_ascii=False, indent=1))
