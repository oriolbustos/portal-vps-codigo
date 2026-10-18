#!/usr/bin/env python3
"""Panel To Do (/todo/): una copia de Microsoft To Do sobre una nota del vault ("30 - Plan/To Do.md").

Formato de la nota (se puede editar a mano en Obsidian):
  ### Tareas                       una lista ("### "); las tareas sin título encima también van a "Tareas"
  ### Casa %%color: verde%%        color elegido de la lista (comentario de Obsidian, no se ve); sin él, uno fijo por nombre
  ## Personal                      un grupo de listas ("## ") con sus "### " debajo; el orden de la nota es el del panel
  - [ ] Llamar al casero ⭐ ☀️ 2026-10-18 📅 2026-10-20 ✅ 2026-10-21
        ⭐ importante · ☀️ fecha = en Mi día ese día · 📅 vence · ✅ completada ese día
  	- [ ] paso                     pasos: casillas con sangría debajo de la tarea
  	> nota                         nota: líneas con sangría que empiezan por "> "
  ## Eliminadas                    lo que se borra desde el portal acaba aquí (no se ve en el panel): las tareas sueltas
                                   arriba y las listas y grupos borrados como "### Lista (Grupo) · eliminada AAAA-MM-DD"

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
COLOR = re.compile(r"\s*%%\s*color:\s*([a-z]+)\s*%%")
COLORES = ("azul", "indigo", "verde", "marron", "morado", "rosa", "oliva", "rojo", "gris")


def _sin_color(titulo):
    """"Casa %%color: verde%%" → ("Casa", "verde")."""
    m = COLOR.search(titulo)
    return (COLOR.sub("", titulo).strip(), m.group(1)) if m else (titulo, "")


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
        actual = {"titulo": titulo, "grupo": "", "n": n, "fin": n, "tareas": [], "color": ""}
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
                titulo, color = _sin_color(titulo)
                actual = {"titulo": titulo, "grupo": grupo, "n": i, "fin": i, "tareas": [], "color": color}
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
            listas.append({"titulo": m.group(2), "grupo": "", "n": i, "fin": i, "tareas": [], "color": ""})
    grupos = [g for g in grupos if any(x["grupo"] == g for x in listas)]
    if not any(x["titulo"] == BANDEJA and not x["grupo"] for x in listas):
        listas.insert(0, {"titulo": BANDEJA, "grupo": "", "n": -1, "fin": -1, "tareas": [], "color": ""})
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


# --- secciones de la nota (listas y grupos) ---

def _nivel(l):
    m = TITULO.match(l)
    return (len(m.group(1)), m.group(2)) if m else (0, "")


def _fin(lineas, i, hasta=6):
    """Dónde acaba la sección que empieza en i: el siguiente título de nivel <= hasta (o el final)."""
    return next((j for j in range(i + 1, len(lineas)) if 0 < _nivel(lineas[j])[0] <= hasta), len(lineas))


def _titulos(lineas, titulo):
    """Líneas "# "/"## " con ese título (un grupo puede salir dos veces si se escribió así a mano)."""
    return [i for i, l in enumerate(lineas) if 0 < (x := _nivel(l))[0] <= 2 and x[1] == titulo]


def _recortar(bloque):
    bloque = list(bloque)
    while bloque and not bloque[0].strip():
        bloque.pop(0)
    while bloque and not bloque[-1].strip():
        bloque.pop()
    return bloque


def _quitar(lineas, a, b):
    """Quita lineas[a:b] y deja una línea en blanco entre lo de antes y lo de después."""
    fuera = lineas[a:b]
    del lineas[a:b]
    if 0 < a < len(lineas) and lineas[a - 1].strip() and lineas[a].strip() and not _nivel(lineas[a - 1])[0]:
        lineas.insert(a, "")
    return fuera


def _meter(lineas, pos, bloque):
    """Mete un bloque (lista o grupo con su título) en pos, separado por líneas en blanco."""
    while pos > 0 and not lineas[pos - 1].strip():
        pos -= 1
    lineas[pos:pos] = ([""] if pos else []) + bloque + ([""] if pos < len(lineas) and lineas[pos].strip() else [])


def _sueltas(lineas):
    """Dónde acaban las listas sin grupo: antes del primer "## " (un "###" debajo sería de ese grupo)."""
    return next((i for i, l in enumerate(lineas) if 0 < _nivel(l)[0] <= 2), len(lineas))


def _antes_de_eliminadas(lineas):
    return (_titulos(lineas, ELIMINADAS) or [len(lineas)])[0]


def _eliminadas(lineas):
    """Línea de "## Eliminadas" (la crea al final si no está)."""
    hay = _titulos(lineas, ELIMINADAS)
    if hay:
        return hay[0]
    while lineas and not lineas[-1].strip():
        lineas.pop()
    lineas += (["", f"## {ELIMINADAS}"] if lineas else [f"## {ELIMINADAS}"])
    return len(lineas) - 1


def _a_eliminadas(lineas, bloques):
    """Lleva listas borradas (cada una con su título "### ...") al final de "## Eliminadas"."""
    for b in bloques:
        e = _eliminadas(lineas)
        _meter(lineas, _fin(lineas, e, 2), b)


def _encabezado(lista, hoy, grupo=None):
    grupo = lista["grupo"] if grupo is None else grupo
    return f"### {lista['titulo']}" + (f" ({grupo})" if grupo else "") + f" · eliminada {hoy}"


def _linea_lista(titulo, color, nivel=3):
    return "#" * nivel + f" {titulo}" + (f" %%color: {color}%%" if color else "")


def _limpiar_grupo(lineas, grupo):
    """Un "## Grupo" que se queda sin nada debajo se quita (si no, se leería como una lista vacía)."""
    if not grupo:
        return
    for i in reversed(_titulos(lineas, grupo)):
        if not any(x.strip() for x in lineas[i + 1:_fin(lineas, i, 2)]):
            _quitar(lineas, i, _fin(lineas, i, 2))


def _libre(modelo, titulo, grupo, salvo=None):
    if titulo == ELIMINADAS or (titulo == BANDEJA and not grupo):
        raise Error(f"«{titulo}» está reservado.")
    if any(l is not salvo and l["titulo"] == titulo and l["grupo"] == grupo for l in modelo["listas"]):
        raise Error(f"Ya hay una lista «{titulo}»" + (f" en {grupo}." if grupo else " sin grupo."))


def _nombre_grupo(modelo, s):
    s = _limpio(s, 100)
    if s == ELIMINADAS or s == BANDEJA:
        raise Error(f"«{s}» está reservado.")
    return s


def _sacar_lista(lineas, modelo, lista):
    """Quita una lista de la nota y devuelve (sus líneas sin título, sus otros datos). No toca la bandeja."""
    if lista["titulo"] == BANDEJA and not lista["grupo"]:
        raise Error("La lista Tareas no se puede cambiar.")
    n = lista["n"]
    nivel = _nivel(lineas[n])[0]
    b = _fin(lineas, n)
    cuerpo = _recortar(lineas[n + 1:b])
    if nivel <= 2 and any(x["grupo"] == lista["titulo"] for x in modelo["listas"]):
        _quitar(lineas, n + 1, b)  # tareas sueltas bajo un "## Grupo": el título es del grupo y se queda
    else:
        _quitar(lineas, n, b)
    _limpiar_grupo(lineas, lista["grupo"])
    return cuerpo


def _secciones_grupo(lineas, grupo):
    """Quita de la nota todas las secciones "## grupo" y devuelve sus trozos [(título o None, líneas)]."""
    trozos = []
    for i in reversed(_titulos(lineas, grupo)):
        cuerpo = _quitar(lineas, i, _fin(lineas, i, 2))[1:]
        parte = [[None, []]]
        for l in cuerpo:
            if _nivel(l)[0]:
                parte.append([_nivel(l)[1], []])
            else:
                parte[-1][1].append(l)
        trozos[0:0] = [(t, _recortar(ls)) for t, ls in parte if t is not None or _recortar(ls)]
    return trozos


def _poner_lista(lineas, modelo, bloque, grupo):
    """Mete una lista ("### ..." + tareas) al final de su grupo, de las sueltas o en un grupo nuevo."""
    if not grupo:
        _meter(lineas, _sueltas(lineas), bloque)
    elif _titulos(lineas, grupo):
        _meter(lineas, _fin(lineas, _titulos(lineas, grupo)[-1], 2), bloque)
    else:
        _meter(lineas, _antes_de_eliminadas(lineas), [f"## {grupo}"] + bloque)


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
        if grupo:
            grupo = _nombre_grupo(modelo, grupo)
        _libre(modelo, titulo, grupo)
        _poner_lista(lineas, modelo, [f"### {titulo}"], grupo)
        return lineas

    if op in ("lista_renombrar", "lista_color", "lista_borrar", "lista_mover_grupo", "lista_ordenar"):
        lista = _lista(modelo, d.get("lista"), d.get("grupo"))
        if lista["titulo"] == BANDEJA and not lista["grupo"]:
            raise Error("La lista Tareas no se puede cambiar.")
        n, nivel = lista["n"], _nivel(lineas[lista["n"]])[0]
        compartida = nivel <= 2 and any(x["grupo"] == lista["titulo"] for x in modelo["listas"])
        if op == "lista_renombrar":
            titulo = _limpio(d.get("titulo"), 100)
            _libre(modelo, titulo, lista["grupo"], salvo=lista)
            if compartida:
                raise Error("Esta lista es el propio título de su grupo; cámbiala en Obsidian.")
            lineas[n] = _linea_lista(titulo, lista["color"], nivel)
        elif op == "lista_color":
            color = d.get("color") or ""
            if color and color not in COLORES:
                raise Error("Color no válido.")
            if compartida:
                raise Error("Esta lista es el propio título de su grupo; cámbiala en Obsidian.")
            lineas[n] = _linea_lista(lista["titulo"], color, nivel)
        elif op == "lista_borrar":
            cuerpo = _sacar_lista(lineas, modelo, lista)
            _a_eliminadas(lineas, [[_encabezado(lista, hoy)] + cuerpo])
        elif op == "lista_mover_grupo":
            destino = re.sub(r"\s+", " ", str(d.get("destino") or "")).strip()
            if destino == lista["grupo"]:
                return lineas
            if destino:
                destino = _nombre_grupo(modelo, destino)
            _libre(modelo, lista["titulo"], destino, salvo=lista)
            cuerpo = _sacar_lista(lineas, modelo, lista)
            _poner_lista(lineas, analizar(lineas), [_linea_lista(lista["titulo"], lista["color"])] + cuerpo, destino)
        else:  # lista_ordenar: junto a otra lista (antes o después), pasando a su grupo
            junto = d.get("junto") or {}
            otra = _lista(modelo, junto.get("lista"), junto.get("grupo"))
            if otra is lista:
                return lineas
            _libre(modelo, lista["titulo"], otra["grupo"], salvo=lista)
            bloque = [_linea_lista(lista["titulo"], lista["color"])] + _sacar_lista(lineas, modelo, lista)
            otra = _lista(analizar(lineas), otra["titulo"], otra["grupo"])
            if otra["n"] == -1 or (otra["titulo"] == BANDEJA and not otra["grupo"]):
                pos = _fin(lineas, otra["n"])  # nada va encima de la bandeja
            elif _nivel(lineas[otra["n"]])[0] <= 2:
                pos = _sueltas(lineas)  # una lista escrita como "## " sin "###": va con las sueltas
            else:
                pos = _fin(lineas, otra["n"]) if d.get("despues") else otra["n"]
            _meter(lineas, pos, bloque)
        return lineas

    if op in ("grupo_renombrar", "grupo_desagrupar", "grupo_borrar", "grupo_ordenar"):
        grupo = d.get("grupo")
        if grupo not in modelo["grupos"]:
            raise Error("Ese grupo ya no está; recarga.")
        suyas = [x for x in modelo["listas"] if x["grupo"] == grupo]
        if op == "grupo_renombrar":
            titulo = _nombre_grupo(modelo, d.get("titulo"))
            if titulo != grupo and titulo in modelo["grupos"]:
                raise Error("Ya hay un grupo con ese nombre.")
            for i in _titulos(lineas, grupo):
                lineas[i] = "#" * _nivel(lineas[i])[0] + f" {titulo}"
        elif op == "grupo_desagrupar":
            sueltas = {x["titulo"] for x in modelo["listas"] if not x["grupo"]} | {BANDEJA}
            if choque := [x["titulo"] for x in suyas if x["titulo"] in sueltas]:
                raise Error(f"Ya hay una lista «{choque[0]}» sin grupo; renombra una antes.")
            bloque = []
            for t, ls in _secciones_grupo(lineas, grupo):
                bloque += ([""] if bloque else []) + [f"### {t}" if t else f"### {grupo}"] + ls
            if bloque:
                _meter(lineas, _sueltas(lineas), bloque)
        elif op == "grupo_borrar":
            bloques = []
            for t, ls in _secciones_grupo(lineas, grupo):
                lista = {"titulo": _sin_color(t)[0] if t else grupo, "grupo": grupo}
                bloques.append([_encabezado(lista, hoy)] + ls)
            _a_eliminadas(lineas, bloques)
        else:  # grupo_ordenar: antes o después de otro grupo
            junto = d.get("junto")
            if junto == grupo:
                return lineas
            if junto not in modelo["grupos"]:
                raise Error("Ese grupo ya no está; recarga.")
            partes = []
            for i in reversed(_titulos(lineas, grupo)):
                partes.insert(0, _recortar(_quitar(lineas, i, _fin(lineas, i, 2))))
            bloque = partes[0] + [x for p in partes[1:] for x in [""] + p[1:]]
            otro = _titulos(lineas, junto)
            _meter(lineas, _fin(lineas, otro[-1], 2) if d.get("despues") else otro[0], bloque)
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
        else:  # las tareas sueltas van arriba de Eliminadas, antes de las listas borradas
            e = _eliminadas(lineas)
            pos = _fin(lineas, e)
            while pos > e + 1 and not lineas[pos - 1].strip():
                pos -= 1
            lineas[pos:pos] = bloque + ([""] if pos < len(lineas) and lineas[pos].strip() else [])
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
