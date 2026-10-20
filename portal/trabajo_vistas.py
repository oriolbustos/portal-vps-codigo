#!/usr/bin/env python3
"""Pestañas de Trabajo además del CV: NTT, Carrera, Proyectos y Comparar CV. Solo leen el vault.

- NTT: secciones de 10 - NTT Data/_system/recent-context.md y las carpetas de proyecto.
- Carrera: 40 - Opportunities/_system/recent-context.md y las carpetas de cada proceso (2025/, 2026/...).
  En modo censurado no existe (lo decide portal.py).
- Proyectos: 50 - Projects y 30 - Education de 20 - Career (cursos, libros, vídeos, eventos...).
- Comparar: diferencias entre dos CV (LaTeX), por líneas y con las palabras cambiadas marcadas.
El Markdown se pinta con archivos.markdown y los enlaces abren la nota en el panel Vault.
"""
import difflib, html, os, re, time
from pathlib import Path

import archivos

VAULT = Path("/srv/vault")
NTT = VAULT / "10 - NTT Data"
CARRERA = VAULT / "20 - Career"
OPORT = CARRERA / "40 - Opportunities"
VIEJO = 14  # días: a partir de aquí una nota de contexto se marca como sin actualizar


def rel(p: Path) -> str:
    return str(p.relative_to(VAULT))


def enlace(p: Path, texto: str = "") -> str:
    return f'<a class="wiki" href="{html.escape(archivos.url("vault", rel(p)))}">{html.escape(texto or p.stem)}</a>'


def fecha(t: float) -> str:
    return time.strftime("%d-%m-%y", time.localtime(t))


def secciones(p: Path) -> tuple:
    """Nota → ({título "## ": líneas}, frontmatter dict). Sin la parte "How this file is updated"."""
    try:
        texto = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}, {}
    fm = {}
    m = re.match(r"^---\n(.*?)\n---\n", texto, re.S)
    if m:
        fm = dict(re.findall(r"^(\w+):\s*(.*)$", m.group(1), re.M))
        texto = texto[m.end():]
    out, actual = {}, None
    for l in texto.splitlines():
        h = re.match(r"^##\s+(.*)$", l)
        if h:
            actual = h.group(1).strip()
            out[actual] = []
        elif actual is not None and l.strip() != "---":
            out[actual].append(l)
    return {k: v for k, v in out.items() if not k.lower().startswith("how this file")}, fm


def bloque(titulo: str, lineas: list, desde: str, n: str, sub: str = "", max_items: int = 0) -> str:
    if max_items:
        items, out = 0, []
        for l in lineas:
            if re.match(r"^- ", l):
                items += 1
                if items > max_items:
                    break
            out.append(l)
        lineas = out
    cuerpo = archivos.markdown("\n".join(lineas), desde).strip()
    if not cuerpo:
        cuerpo = '<p class="secundario">-</p>'
    return (f'<div class="seccion"><span class="n">{n}</span><h2>{html.escape(titulo)}</h2>'
            + (f"<p>{sub}</p>" if sub else "") + f'</div><div class="nota-ctx">{cuerpo}</div>')


def aviso_fecha(fm: dict, p: Path) -> str:
    f = fm.get("updated", "")
    try:
        dias = int((time.time() - time.mktime(time.strptime(f, "%Y-%m-%d"))) // 86400)
    except ValueError:
        return ""
    txt = f"Contexto actualizado el {f[8:10]}-{f[5:7]}" + (f", hace {dias} días" if dias > 1 else "")
    cls = "insignia punto" if dias > VIEJO else "insignia"
    return f'<p class="ctx-fecha"><span class="{cls}">{txt}</span> {enlace(p, "Abrir la nota")}</p>'


def carpetas(base: Path, excluir=(), solo_dirs=False) -> list:
    """Hijos de base → [{p, nombre, notas, cambio}] ordenados por último cambio."""
    out = []
    try:
        hijos = list(base.iterdir())
    except OSError:
        return out
    for h in hijos:
        if h.name.startswith((".", "_")) or h.name in excluir or (solo_dirs and not h.is_dir()):
            continue
        if h.is_dir():
            mds = [Path(r) / a for r, _, fs in os.walk(h) for a in fs if a.endswith(".md")]
            if not mds:
                continue
            ult = max(mds, key=lambda x: x.stat().st_mtime)
            out.append({"p": h, "nombre": h.name, "notas": len(mds), "cambio": ult.stat().st_mtime, "ultima": ult})
        elif h.suffix == ".md":
            out.append({"p": h, "nombre": h.stem, "notas": 1, "cambio": h.stat().st_mtime, "ultima": h})
    return sorted(out, key=lambda x: -x["cambio"])


def tabla_carpetas(filas: list, cab="Proyecto") -> str:
    if not filas:
        return '<p class="vacio">-</p>'
    tr = "".join(
        f'<tr><td>{enlace(f["ultima"] if f["p"].is_dir() else f["p"], f["nombre"])}</td>'
        f'<td class="n">{f["notas"]}</td><td class="n">{fecha(f["cambio"])}</td>'
        f'<td class="peq secundario">{html.escape(f["ultima"].stem) if f["p"].is_dir() else ""}</td></tr>' for f in filas)
    return (f'<div class="tabla"><table class="lista-t"><tr><th>{cab}</th><th class="n">Notas</th><th class="n">Último cambio</th>'
            f'<th>Nota más reciente</th></tr>{tr}</table></div>')


# ---------- pestañas ----------
def ntt() -> str:
    p = NTT / "_system" / "recent-context.md"
    s, fm = secciones(p)
    d = rel(p)
    get = lambda *ks: next((v for k, v in s.items() if any(k.lower().startswith(x) for x in ks)), [])
    proyectos = carpetas(NTT, excluir=("Cerrado", "Otros", "Cosas", "Entrevistas", "Puesto", "Reuniones"), solo_dirs=True)
    return (aviso_fecha(fm, p)
            + bloque("Ahora", get("current"), d, "01", "Qué tienes entre manos.")
            + bloque("Hilos abiertos", get("open"), d, "02", "Lo pendiente de cerrar o de respuesta.")
            + bloque("Último", get("recent"), d, "03", "Lo más reciente, de nuevo a viejo. Solo los 6 últimos.", max_items=6)
            + '<div class="seccion"><span class="n">04</span><h2>Proyectos</h2><p>Carpetas de 10 - NTT Data, por último cambio. Las cerradas están en Cerrado.</p></div>'
            + tabla_carpetas(proyectos))


def carrera() -> str:
    p = OPORT / "_system" / "recent-context.md"
    s, fm = secciones(p)
    d = rel(p)
    get = lambda *ks: next((v for k, v in s.items() if any(k.lower().startswith(x) for x in ks)), [])
    procesos = []
    for ano in sorted((x for x in OPORT.iterdir() if x.is_dir() and re.fullmatch(r"\d{4}", x.name)), reverse=True):
        for f in carpetas(ano, solo_dirs=True):
            f["nombre"] = f"{f['nombre']} ({ano.name})"
            procesos.append(f)
    procesos.sort(key=lambda x: -x["cambio"])
    return (aviso_fecha(fm, p)
            + bloque("Activos", get("active"), d, "01", "Procesos en marcha y en qué punto están.")
            + bloque("En pausa", get("dormant"), d, "02")
            + bloque("Hilos abiertos", get("open"), d, "03", "Lo siguiente que toca hacer.")
            + '<div class="seccion"><span class="n">04</span><h2>Carpetas</h2><p>Una por proceso, por último cambio.</p></div>'
            + tabla_carpetas(procesos, "Proceso"))


def proyectos() -> str:
    edu = CARRERA / "30 - Education"
    out = ('<div class="seccion"><span class="n">01</span><h2>Proyectos</h2><p>Proyectos personales de 50 - Projects, por último cambio.</p></div>'
           + tabla_carpetas(carpetas(CARRERA / "50 - Projects", excluir=("Legacy",))))
    nombres = {"courses": "Cursos", "books": "Libros", "videos": "Vídeos", "events": "Eventos", "papers": "Papers",
               "master": "Máster", "subjects": "Asignaturas", "software": "Software", "Guides": "Guías", "Concepts": "Conceptos"}
    grupos = []
    for k, t in nombres.items():
        filas = carpetas(edu / k)
        if filas:
            grupos.append((t, filas))
    grupos.sort(key=lambda g: -g[1][0]["cambio"])
    for n, (t, filas) in enumerate(grupos, 2):
        out += (f'<div class="seccion"><span class="n">{n:02d}</span><h2>{t}</h2><p>De 30 - Education/, por último cambio.</p></div>'
                + tabla_carpetas(filas[:12], t[:-1] if t.endswith("s") else t))
    legado = carpetas(CARRERA / "50 - Projects" / "Legacy")
    if legado:
        out += (f'<details class="legado"><summary>Proyectos antiguos ({len(legado)})</summary>{tabla_carpetas(legado)}</details>')
    return out


# ---------- comparar CV ----------
def _palabras(a: str, b: str) -> tuple:
    ta, tb = re.findall(r"\s+|\w+|[^\w\s]", a), re.findall(r"\s+|\w+|[^\w\s]", b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    oa, ob = [], []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        xa, xb = html.escape("".join(ta[i1:i2])), html.escape("".join(tb[j1:j2]))
        if op == "equal":
            oa.append(xa); ob.append(xb)
        else:
            if xa: oa.append(f"<del>{xa}</del>")
            if xb: ob.append(f"<ins>{xb}</ins>")
    return "".join(oa), "".join(ob)


def texto_cv(t: str) -> list:
    """LaTeX del CV → líneas del texto que se lee (sin preámbulo, comentarios ni comandos)."""
    t = t.split("\\begin{document}", 1)[-1].split("\\end{document}", 1)[0]
    t = re.sub(r"(?<!\\)%.*", "", t)
    t = re.sub(r"\\(sub)*section\*?\{([^{}]*)\}", lambda m: "\n\n" + m.group(2).upper() + "\n", t)
    t = re.sub(r"\\href\{[^{}]*\}", "", t)
    # comandos de formato: fuera con sus argumentos (colores, tamaños, espacios, entornos, reglas)
    t = re.sub(r"\\(color|fontsize|vspace|hspace|rule|begin|end|setlength|addtolength|titlerule|definecolor|usepackage|setlist)\*?(\[[^\]]*\])?(\{[^{}]*\})*", " ", t)
    t = re.sub(r"\\textcolor\{[^{}]*\}", "", t)
    t = re.sub(r"\$[^$]*\$", " ", t).replace("\\ ", " ")
    t = t.replace("}{", "} · {")
    t = t.replace("\\item", "\n- ").replace("\\\\", "\n").replace("\\newline", "\n").replace("\\par", "\n")
    for _ in range(8):  # \cmd[opc]{texto} → texto, de dentro afuera
        t2 = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?\{([^{}]*)\}", r"\2", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"\\[&%$#_]", lambda m: m.group(0)[1], t)
    t = re.sub(r"\\[a-zA-Z]+\*?(\[[^\]]*\])?", " ", t)
    t = t.replace("{", "").replace("}", "").replace("~", " ").replace("---", "—").replace("--", "–").replace("&", " · ")
    out = []
    for l in t.splitlines():
        l = re.sub(r"\s+", " ", l).strip(" ·|")
        l = re.sub(r"\b\d+(\.\d+)?\s*(pt|em|ex|cm|mm|in)\b", "", l)
        l = re.sub(r"\[[^\]]*=[^\]]*\]", "", l)                       # opciones tipo [leftmargin=*]
        l = re.sub(r"(mailto:|https?://)\S+\s*·\s*", "", l)             # \href{url}{texto}: queda solo el texto
        l = re.sub(r"\s*\\+\s*$", "", l).strip(" ·|\\")
        if l and re.search(r"[A-Za-zÀ-ÿ]{2}", l) and not re.fullmatch(r"[a-z]+\d*", l):
            out.append(l)
    return out


def comparar(lista_cv: list, a: str, b: str, modo: str = "texto") -> str:
    """lista_cv = trabajo.lista() → [(nombre, Path)]. a = el antiguo, b = el nuevo."""
    nombres = [n for n, _ in lista_cv]
    if len(nombres) < 2:
        return '<p class="vacio">Hace falta tener al menos dos CV.</p>'
    b = b if b in nombres else nombres[0]
    a = a if a in nombres and a != b else next(n for n in nombres if n != b)
    pa, pb = dict(lista_cv)[a], dict(lista_cv)[b]
    modo = "latex" if modo == "latex" else "texto"
    la, lb = (pa.read_text(encoding="utf-8", errors="replace"), pb.read_text(encoding="utf-8", errors="replace"))
    la, lb = (la.splitlines(), lb.splitlines()) if modo == "latex" else (texto_cv(la), texto_cv(lb))
    opt = lambda sel: "".join(f'<option value="{html.escape(n)}"{" selected" if n == sel else ""}>{html.escape(n)}</option>' for n in nombres)
    cab = f"""<form class="barra-cv" method="get" action="/trabajo/comparar">
<label class="etiqueta" for="ca">Antes</label><select id="ca" name="a" onchange="this.form.submit()">{opt(a)}</select>
<label class="etiqueta" for="cb">Después</label><select id="cb" name="b" onchange="this.form.submit()">{opt(b)}</select>
<input type="hidden" name="modo" value="{modo}">
<a class="boton" href="/trabajo/comparar?a={html.escape(b)}&amp;b={html.escape(a)}&amp;modo={modo}">Invertir</a>
<span class="segmentos"><a class="boton{' activo' if modo == 'texto' else ''}" href="/trabajo/comparar?a={html.escape(a)}&amp;b={html.escape(b)}&amp;modo=texto">Texto</a><a class="boton{' activo' if modo == 'latex' else ''}" href="/trabajo/comparar?a={html.escape(a)}&amp;b={html.escape(b)}&amp;modo=latex">LaTeX</a></span>
<a class="boton crear" href="/trabajo/?cv={html.escape(b)}">Volver al CV</a></form>"""
    sm = difflib.SequenceMatcher(None, [x.strip() for x in la], [x.strip() for x in lb], autojunk=False)
    bloques, quitadas, puestas = [], 0, 0
    for g in sm.get_grouped_opcodes(1):
        filas = []
        for op, i1, i2, j1, j2 in g:
            if op == "equal":
                for k in range(i1, i2):
                    filas.append(f'<tr class="ig"><td class="ln">{k + 1}</td><td class="ln">{j1 + k - i1 + 1}</td><td>{html.escape(la[k].strip()) or "&nbsp;"}</td></tr>')
            elif op == "replace" and i2 - i1 == j2 - j1:
                for k in range(i2 - i1):
                    xa, xb = _palabras(la[i1 + k].strip(), lb[j1 + k].strip())
                    filas.append(f'<tr class="qu"><td class="ln">{i1 + k + 1}</td><td class="ln"></td><td>{xa}</td></tr>')
                    filas.append(f'<tr class="po"><td class="ln"></td><td class="ln">{j1 + k + 1}</td><td>{xb}</td></tr>')
                quitadas += i2 - i1; puestas += j2 - j1
            else:
                for k in range(i1, i2):
                    filas.append(f'<tr class="qu"><td class="ln">{k + 1}</td><td class="ln"></td><td><del>{html.escape(la[k].strip())}</del></td></tr>')
                for k in range(j1, j2):
                    filas.append(f'<tr class="po"><td class="ln"></td><td class="ln">{k + 1}</td><td><ins>{html.escape(lb[k].strip())}</ins></td></tr>')
                quitadas += i2 - i1; puestas += j2 - j1
        bloques.append("".join(filas))
    if not bloques:
        return cab + '<p class="vacio">Son iguales.</p>'
    que = "el texto que se lee en el CV, sin comandos de LaTeX" if modo == "texto" else "el LaTeX línea a línea, formato incluido"
    n = len(bloques)
    resumen = (f'<p class="mini secundario resumen-dif">Compara {que}. {n} {"zona cambiada" if n == 1 else "zonas cambiadas"}: '
               f'{quitadas} líneas antes, {puestas} después. En rojo lo que había antes, en verde lo de después.</p>')
    return cab + resumen + f'<div class="dif {modo}">' + "".join(f"<table><tbody>{b_}</tbody></table>" for b_ in bloques) + "</div>"


ESTILO = """<style>
.nota-ctx { max-width: 80ch; line-height: 1.6; font-size: var(--t-sm); }
.nota-ctx ul { padding-left: 1.2em; margin: 0; } .nota-ctx li { margin: 4px 0; } .nota-ctx p { margin: 6px 0; }
.nota-ctx a { color: var(--tinta-alta); text-underline-offset: 3px; text-decoration-style: dotted; }
.nota-ctx code { font-family: var(--mono); font-size: .9em; } .nota-ctx .etq { color: var(--tinta-2); }
.ctx-fecha { margin-top: 24px; font-size: var(--t-sm); display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.ctx-fecha a { color: var(--tinta-2); }
table.lista-t { width: 100%; border-collapse: collapse; font-size: var(--t-sm); }
.lista-t th { text-align: left; font-weight: 500; color: var(--tinta-2); font-size: var(--t-xs); text-transform: uppercase; letter-spacing: .05em; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--linea); }
.lista-t td { padding: 7px 12px 7px 0; border-bottom: 1px solid var(--linea); vertical-align: top; }
.lista-t td.n, .lista-t th.n { text-align: right; font-family: var(--mono); font-weight: 300; white-space: nowrap; font-size: var(--t-xs); }
.lista-t a { color: var(--tinta-alta); text-decoration: none; } .lista-t a:hover { text-decoration: underline; }
details.legado { margin-top: 32px; } details.legado summary { cursor: pointer; color: var(--tinta-2); font-size: var(--t-sm); }
.resumen-dif { margin: 4px 0 12px; }
.segmentos .boton.activo { background: var(--tinta-alta); color: var(--fondo); border-color: var(--tinta-alta); }
.dif { border: 1px solid var(--linea); background: var(--fondo-alto); overflow-x: auto; }
.dif table { width: 100%; border-collapse: collapse; font: 300 12.5px/1.55 var(--mono); }
.dif.texto table { font: 400 13.5px/1.5 var(--sans); }
.dif table + table { border-top: 1px dashed var(--linea-alta); }
.dif td { padding: 1px 10px; white-space: pre-wrap; overflow-wrap: anywhere; vertical-align: top; }
.dif td.ln { width: 1%; white-space: nowrap; text-align: right; color: var(--tinta-2); user-select: none; padding: 1px 6px; }
.dif tr.ig td:last-child { color: var(--tinta-2); }
.dif tr.qu { background: color-mix(in srgb, var(--c2) 9%, transparent); } .dif tr.po { background: color-mix(in srgb, var(--c3) 11%, transparent); }
.dif del { background: color-mix(in srgb, var(--c2) 28%, transparent); text-decoration: none; color: var(--tinta-alta); }
.dif ins { background: color-mix(in srgb, var(--c3) 32%, transparent); text-decoration: none; color: var(--tinta-alta); }
</style>"""
