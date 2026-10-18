#!/usr/bin/env python3
"""Panel To Do (/todo/): una copia de Microsoft To Do sobre una nota del vault ("30 - Plan/To Do.md").

Formato de la nota (se puede editar a mano en Obsidian):
  ### Tareas                       una lista ("### "); las tareas sin título encima también van a "Tareas"
  ## Personal                      un grupo de listas ("## ") con sus "### " debajo
  - [ ] Llamar al casero ⭐ ☀️ 2026-10-18 📅 2026-10-20 ✅ 2026-10-21
        ⭐ importante · ☀️ fecha = en Mi día ese día · 📅 vence · ✅ completada ese día
  	- [ ] paso                     pasos: casillas con sangría debajo de la tarea
  	> nota                         nota: líneas con sangría que empiezan por "> "
  ## Eliminadas                    lo que se borra desde el portal acaba aquí (no se ve en el panel)

Escribir: el portal no puede tocar el vault. Cada cambio se aplica aquí sobre la nota entera y se deja en
/var/lib/portal/todo-pendiente/; todo-guardar.path → /opt/portal/todo-guardar.py (root) la escribe solo si
la nota no ha cambiado desde que se leyó (base = sha256).
"""
import hashlib, json, os, re, secrets, time
from pathlib import Path

NOTA = Path("/srv/vault/30 - Personal/30 - Plan/To Do.md")
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "todo-pendiente"
RESULTADO = ESTADO / "todo-resultado.json"
ELIMINADAS = "Eliminadas"
BANDEJA = "Tareas"
TAREA = re.compile(r"^- \[([ xX])\] ?(.*)$")
PASO = re.compile(r"^\s+- \[([ xX])\] ?(.*)$")
TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
F = r"(\d{4}-\d{2}-\d{2})"
MARCAS = {"midia": "☀️", "vence": "📅", "hecha": "✅"}
ES_FECHA = re.compile(r"\d{4}-\d{2}-\d{2}")


# ---------- leer ----------

def partir(texto: str) -> dict:
    t = {"importante": False, "midia": "", "vence": "", "hecha": ""}
    for clave, marca in MARCAS.items():
        m = re.search(rf"\s*{marca}️?\s*{F}", texto)
        if m:
            t[clave], texto = m.group(1), texto[:m.start()] + texto[m.end():]
    if re.search(r"\s*⭐", texto):
        t["importante"], texto = True, re.sub(r"\s*⭐️?", "", texto)
    t["titulo"] = re.sub(r"\s+", " ", texto).strip()
    return t


def componer(t: dict) -> str:
    s = f"- [{'x' if t['hecho'] else ' '}] {t['titulo']}"
    if t["importante"]:
        s += " ⭐"
    for clave in ("midia", "vence"):
        if t[clave]:
            s += f" {MARCAS[clave]} {t[clave]}"
    if t["hecho"] and t.get("hecha"):
        s += f" ✅ {t['hecha']}"
    return s


def analizar(lineas: list) -> dict:
    """→ {grupos: [nombres], listas: [{titulo, grupo, n, fin, tareas: [...]}]}. n = línea del título (-1 = sin título)."""
    listas, grupos = [], []
    grupo, actual, tarea, eliminadas = "", None, None, False

    def lista_implicita(n):
        nonlocal actual
        titulo = grupo or BANDEJA
        actual = {"titulo": titulo, "grupo": "", "n": n, "fin": n, "tareas": []}
        listas.append(actual)

    titulo_grupo = -1
    for i, l in enumerate(lineas):
        m = TITULO.match(l)
        if m:
            tarea = None
            nivel, titulo = len(m.group(1)), m.group(2)
            if nivel <= 2:
                eliminadas = titulo == ELIMINADAS
                grupo = "" if eliminadas else titulo
                if grupo and grupo not in grupos:
                    grupos.append(grupo)
                actual, titulo_grupo = None, i
            elif not eliminadas:
                actual = {"titulo": titulo, "grupo": grupo, "n": i, "fin": i, "tareas": []}
                listas.append(actual)
            continue
        if eliminadas:
            continue
        m = TAREA.match(l)
        if m:
            if actual is None:
                lista_implicita(titulo_grupo)
            tarea = partir(m.group(2)) | {"n": i, "fin": i, "hecho": m.group(1) != " ", "pasos": [], "nota": []}
            actual["tareas"].append(tarea)
            actual["fin"] = i
            continue
        if tarea and l.strip() and l[:1] in (" ", "\t"):
            p = PASO.match(l)
            if p:
                tarea["pasos"].append({"n": i, "hecho": p.group(1) != " ", "texto": p.group(2).strip()})
            else:
                tarea["nota"].append(re.sub(r"^\s*(>\s?|-\s+)?", "", l))
            tarea["fin"] = actual["fin"] = i
            continue
        if l.strip():
            tarea = None
    # los grupos sin "###" dentro también son listas (con el nombre del grupo)
    for i, l in enumerate(lineas):
        m = TITULO.match(l)
        if m and len(m.group(1)) <= 2 and m.group(2) != ELIMINADAS and \
                not any(x["grupo"] == m.group(2) or (x["n"] == i) for x in listas):
            listas.append({"titulo": m.group(2), "grupo": "", "n": i, "fin": i, "tareas": []})
    grupos = [g for g in grupos if any(x["grupo"] == g for x in listas)]
    if not any(x["titulo"] == BANDEJA and not x["grupo"] for x in listas):
        listas.insert(0, {"titulo": BANDEJA, "grupo": "", "n": -1, "fin": -1, "tareas": []})
    listas.sort(key=lambda x: (x["titulo"] != BANDEJA or bool(x["grupo"]), x["n"]))
    return {"grupos": grupos, "listas": listas}


def _leer():
    crudo = NOTA.read_bytes()
    texto = crudo.decode("utf-8", errors="replace")
    nl = "\r\n" if "\r\n" in texto else "\n"
    lineas = texto.split(nl)
    final = len(lineas) > 1 and lineas[-1] == ""
    if final:
        lineas.pop()
    return lineas, nl, final, hashlib.sha256(crudo).hexdigest()


def datos() -> dict:
    try:
        lineas, _, _, base = _leer()
    except OSError as e:
        return {"error": f"No puedo leer {NOTA.name}: {e}", "listas": [], "grupos": [], "base": ""}
    return analizar(lineas) | {"base": base, "nota": str(NOTA).split("/srv/vault/")[-1], "leida": time.strftime("%H:%M")}


# ---------- editar ----------

class Error(Exception):
    pass


def _tarea(modelo, n):
    for l in modelo["listas"]:
        for t in l["tareas"]:
            if t["n"] == n:
                return l, t
    raise Error("Esa tarea ya no está; recarga.")


def _lista(modelo, titulo, grupo):
    for l in modelo["listas"]:
        if l["titulo"] == titulo and l["grupo"] == (grupo or ""):
            return l
    raise Error("Esa lista ya no está; recarga.")


def _limpio(s, largo=500):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    if not s or len(s) > largo:
        raise Error("Texto vacío o demasiado largo.")
    return s


def _fecha(s):
    if s in (None, ""):
        return ""
    if not isinstance(s, str) or not ES_FECHA.fullmatch(s):
        raise Error("Fecha no válida.")
    return s


def _insertar_en_lista(lineas, lista, bloque):
    if lista["n"] == -1 and not lista["tareas"]:  # "Tareas" sin título: arriba del todo
        pos = 0
        lineas[pos:pos] = [f"### {BANDEJA}"] + bloque + ([""] if lineas and lineas[0].strip() else [])
        return
    pos = lista["fin"] + 1
    lineas[pos:pos] = bloque


def aplicar(lineas: list, d: dict) -> list:
    """Aplica la operación d sobre las líneas de la nota y devuelve las nuevas líneas."""
    lineas = list(lineas)
    modelo = analizar(lineas)
    op, hoy = d.get("op"), _fecha(d.get("hoy")) or time.strftime("%Y-%m-%d")

    if op == "anadir":
        lista = _lista(modelo, d.get("lista"), d.get("grupo"))
        t = {"titulo": _limpio(d.get("texto")), "hecho": False, "importante": bool(d.get("importante")),
             "midia": hoy if d.get("midia") else "", "vence": _fecha(d.get("vence"))}
        _insertar_en_lista(lineas, lista, [componer(t)])
        return lineas

    if op == "lista_nueva":
        titulo, grupo = _limpio(d.get("titulo"), 100), re.sub(r"\s+", " ", str(d.get("grupo") or "")).strip()
        if titulo in (ELIMINADAS,) or any(l["titulo"] == titulo and l["grupo"] == grupo for l in modelo["listas"]):
            raise Error("Ya hay una lista con ese nombre.")
        fin = len(lineas)
        elim = next((i for i, l in enumerate(lineas) if (m := TITULO.match(l)) and len(m.group(1)) <= 2 and m.group(2) == ELIMINADAS), None)
        if grupo and grupo in modelo["grupos"]:
            ini = next(i for i, l in enumerate(lineas) if (m := TITULO.match(l)) and len(m.group(1)) <= 2 and m.group(2) == grupo)
            fin = next((i for i in range(ini + 1, len(lineas)) if (m := TITULO.match(lineas[i])) and len(m.group(1)) <= 2), len(lineas))
            nuevo = [f"### {titulo}"]
        else:
            if elim is not None:
                fin = elim
            nuevo = ([f"## {grupo}"] if grupo else []) + [f"### {titulo}"]
        while fin > 0 and not lineas[fin - 1].strip():
            fin -= 1
        lineas[fin:fin] = ([""] if fin else []) + nuevo + ([""] if fin < len(lineas) else [])
        return lineas

    n = d.get("n")
    if not isinstance(n, int):
        raise Error("Petición no válida.")
    lista, t = _tarea(modelo, n)

    def reescribir():
        lineas[t["n"]] = componer(t)

    if op == "marcar":
        t["hecho"] = bool(d.get("hecho"))
        t["hecha"] = hoy if t["hecho"] else ""
        reescribir()
    elif op == "importante":
        t["importante"] = bool(d.get("valor"))
        reescribir()
    elif op == "midia":
        t["midia"] = hoy if d.get("valor") else ""
        reescribir()
    elif op == "vence":
        t["vence"] = _fecha(d.get("fecha"))
        reescribir()
    elif op == "renombrar":
        t["titulo"] = _limpio(d.get("texto"))
        reescribir()
    elif op == "paso_anadir":
        pos = (t["pasos"][-1]["n"] if t["pasos"] else t["n"]) + 1
        lineas.insert(pos, f"\t- [ ] {_limpio(d.get('texto'))}")
    elif op in ("paso_marcar", "paso_borrar", "paso_renombrar"):
        i = d.get("i")
        if not isinstance(i, int) or not 0 <= i < len(t["pasos"]):
            raise Error("Ese paso ya no está; recarga.")
        p = t["pasos"][i]
        if op == "paso_borrar":
            del lineas[p["n"]]
        else:
            hecho = bool(d.get("hecho")) if op == "paso_marcar" else p["hecho"]
            texto = _limpio(d.get("texto")) if op == "paso_renombrar" else p["texto"]
            lineas[p["n"]] = f"\t- [{'x' if hecho else ' '}] {texto}"
    elif op == "nota":
        texto = str(d.get("texto") or "").replace("\r", "").strip("\n")
        if len(texto) > 5000:
            raise Error("Nota demasiado larga.")
        hijos = [i for i in range(t["n"] + 1, t["fin"] + 1) if not PASO.match(lineas[i])]
        for i in reversed(hijos):
            del lineas[i]
        pasos_fin = t["n"] + sum(1 for i in range(t["n"] + 1, t["fin"] + 1) if i not in hijos)
        nuevas = [f"\t> {x}".rstrip() for x in texto.split("\n")] if texto.strip() else []
        lineas[pasos_fin + 1:pasos_fin + 1] = nuevas
    elif op in ("borrar", "mover"):
        bloque = lineas[t["n"]:t["fin"] + 1]
        del lineas[t["n"]:t["fin"] + 1]
        if op == "mover":
            destino = _lista(analizar(lineas), d.get("lista"), d.get("grupo"))
            _insertar_en_lista(lineas, destino, bloque)
        else:
            if not any((m := TITULO.match(l)) and len(m.group(1)) <= 2 and m.group(2) == ELIMINADAS for l in lineas):
                while lineas and not lineas[-1].strip():
                    lineas.pop()
                lineas += ["", f"## {ELIMINADAS}"]
            lineas += bloque
    else:
        raise Error("Operación desconocida.")
    return lineas


def guardar(base: str, texto: str):
    """Deja la nota editada para todo-guardar y espera su respuesta → (ok, error)."""
    pid = secrets.token_hex(8)
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"todo-{pid}.tmp"
    tmp.write_text(json.dumps({"id": pid, "base": base, "texto": texto}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(50):  # todo-guardar.path lo recoge en menos de un segundo
        time.sleep(0.1)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return r.get("ok"), r.get("error", "")
    return False, "El guardado no responde. Revisa todo-guardar en el servidor."


def operar(d: dict):
    """d = {op, base, ...} → (código, respuesta con los datos nuevos)."""
    if not isinstance(d, dict):
        return 400, {"ok": False, "mensaje": "Petición no válida."}
    try:
        lineas, nl, final, base = _leer()
        if d.get("base") != base:
            return 409, {"ok": False, "mensaje": "La nota ha cambiado; la he recargado.", "datos": datos()}
        nuevas = aplicar(lineas, d)
    except Error as e:
        return 409, {"ok": False, "mensaje": str(e), "datos": datos()}
    except OSError as e:
        return 500, {"ok": False, "mensaje": f"No puedo leer la nota: {e}"}
    ok, error = guardar(base, nl.join(nuevas) + (nl if final else ""))
    if not ok:
        return 409, {"ok": False, "mensaje": error or "No se ha guardado.", "datos": datos()}
    return 200, {"ok": True, "datos": datos()}


def html(plantilla: Path) -> str:
    js = json.dumps(datos(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)
