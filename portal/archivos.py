#!/usr/bin/env python3
"""Exploradores de solo lectura: el panel Vault (/vault/) y los archivos del VPS en Sistema (/sistema/archivos/).

- Cada uno va detrás de su propia contraseña (/etc/portal/clave-vault y /etc/portal/clave-sistema), distintas
  de la del portal y de la caja. Abierto dura 30 min desde el último uso, por sesión. En modo censurado no existen.
- El explorador del VPS no entra en /srv/vault: para eso está el panel Vault.
- Solo lee. Lo que el usuario `portal` no puede leer (por permisos de Unix) no se ve. Además se bloquean
  por código las rutas con secretos que `portal` sí podría leer (su propia contraseña, sesiones, tokens).
- Markdown: frontmatter, títulos, listas con casillas, tablas, código, citas y callouts, [[enlaces]] e
  ![[incrustados]] resueltos por nombre dentro del vault, y "Enlazan aquí" (backlinks).
"""
import html, mimetypes, os, re, stat, threading, time
from pathlib import Path
from urllib.parse import quote, urlencode

RAICES = {"vault": ("Vault", Path("/srv/vault")), "vps": ("VPS", Path("/"))}
PREFIJO = {"vault": "/vault/", "vps": "/sistema/archivos/"}
CLAVES = {"vault": Path("/etc/portal/clave-vault"), "vps": Path("/etc/portal/clave-sistema")}
TITULO = {"vault": ("Vault", "vault", "/", "Portal"), "vps": ("Archivos del VPS", "sistema", "/sistema/", "Sistema")}
DURACION = 30 * 60
MAX_TEXTO = 1_000_000          # más de 1 MB no se pinta como texto
MAX_CRUDO = 25_000_000         # ni se sirve en crudo
MAX_LISTA = 2000
PROHIBIDO = [Path(p) for p in ("/proc", "/sys", "/dev", "/run", "/etc/portal", "/etc/shadow", "/etc/gshadow",
                                 "/etc/salud-sync", "/var/lib/portal", "/srv/finanzas", "/root", "/home",
                                 "/opt/salud/.env", "/var/lib/docker", "/boot/efi", "/swapfile")]
NOMBRE_SECRETO = re.compile(r"(^\.env(\..*)?$|^id_[\w-]+$|\.(pem|key|p12|pfx|kdbx)$|^(shadow|gshadow)(-)?$|token|secret|clave)", re.I)
IMAGEN = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif"}
OCULTAS_VAULT = {".git", ".trash"}


# ---------- rutas ----------
def prohibida(p: Path, raiz: str = "vault") -> bool:
    if raiz == "vps" and (p == RAICES["vault"][1] or RAICES["vault"][1] in p.parents):
        return True
    if any(p == x or x in p.parents for x in PROHIBIDO):
        return True
    return bool(NOMBRE_SECRETO.search(p.name)) and p.name.lower() not in ("tokens.md",)


def resolver(raiz: str, rel: str):
    """→ (Path real, ruta relativa limpia) o (None, motivo)."""
    if raiz not in RAICES:
        return None, "Esa raíz no existe."
    base = RAICES[raiz][1]
    rel = "/".join(t for t in (rel or "").split("/") if t and t not in (".", ".."))
    try:
        real = Path(os.path.realpath(base / rel))
    except (OSError, ValueError):
        return None, "Ruta no válida."
    if real != base and base not in real.parents:
        return None, "Fuera de la raíz."
    if prohibida(real, raiz):
        return None, "El vault se ve en su panel." if raiz == "vps" and real.is_relative_to(RAICES["vault"][1]) else "Ruta bloqueada: puede contener secretos."
    return real, rel


def url(raiz, rel="", **extra):
    q = {"p": rel} if rel else {}
    q.update(extra)
    return PREFIJO[raiz] + ("?" + urlencode(q, quote_via=quote) if q else "")


def tam(b):
    for u in ("B", "KB", "MB", "GB"):
        if b < 1024 or u == "GB":
            return (f"{b:.0f} {u}" if u in ("B", "KB") else f"{b:.1f} {u}").replace(".", ",")
        b /= 1024


def fecha(t):
    return time.strftime("%d-%m-%y %H:%M", time.localtime(t))


# ---------- índice del vault (para [[enlaces]] y backlinks) ----------
_indice = {"t": 0, "nombres": {}, "enlaces": {}}
_cerrojo = threading.Lock()
ENLACE = re.compile(r"!?\[\[([^\]|#^]+)(?:[#^][^\]|]*)?(?:\|[^\]]*)?\]\]")


def indice():
    with _cerrojo:
        if time.time() - _indice["t"] < 300:
            return _indice
        base = RAICES["vault"][1]
        nombres, enlaces = {}, {}
        for raiz, dirs, archivos in os.walk(base):
            dirs[:] = [d for d in dirs if d not in OCULTAS_VAULT and not d.startswith(".")]
            for a in archivos:
                p = Path(raiz) / a
                rel = str(p.relative_to(base))
                nombres.setdefault(a.lower(), rel)
                if a.endswith(".md"):
                    nombres.setdefault(a[:-3].lower(), rel)
                    try:
                        t = p.read_text(encoding="utf-8", errors="replace")
                    except OSError:
                        continue
                    for m in set(ENLACE.findall(t)):
                        enlaces.setdefault(m.strip().split("/")[-1].lower(), set()).add(rel)
        _indice.update(t=time.time(), nombres=nombres, enlaces=enlaces)
        return _indice


def buscar_nota(nombre: str, desde: str = ""):
    n = nombre.strip()
    base = RAICES["vault"][1]
    if desde:  # ruta relativa a la nota o al vault
        for c in (Path(desde).parent / n, Path(n)):
            for cand in (c, c.with_name(c.name + ".md")):
                if (base / cand).is_file():
                    return str(cand)
    return indice()["nombres"].get(n.split("/")[-1].lower())


# ---------- Markdown ----------
def _inline(t: str, desde: str) -> str:
    t = html.escape(t, quote=False)
    guard = []

    def guardar(s):
        guard.append(s)
        return f"\x00{len(guard) - 1}\x00"
    t = re.sub(r"`([^`]+)`", lambda m: guardar(f"<code>{m.group(1)}</code>"), t)

    def wiki(m):
        emb, destino, alias = m.group(1), m.group(2), m.group(4)
        nombre = destino.split("#")[0].split("^")[0]
        rel = buscar_nota(html.unescape(nombre), desde) if nombre else desde
        texto = alias or destino
        if not rel:
            return guardar(f'<span class="roto" title="No existe">{texto}</span>')
        if emb and Path(rel).suffix.lower() in IMAGEN:
            return guardar(f'<img class="incrustada" src="{html.escape(url("vault", rel, crudo="1"))}" alt="{texto}" loading="lazy">')
        return guardar(f'<a class="wiki" href="{html.escape(url("vault", rel))}">{texto}</a>')
    t = re.sub(r"(!?)\[\[([^\]|]+?)(\|([^\]]+))?\]\]", wiki, t)

    def md_link(m):
        emb, txt, dest = m.group(1), m.group(2), html.unescape(m.group(3)).strip()
        if re.match(r"https?://", dest):
            if emb:
                return guardar(f'<img class="incrustada" src="{html.escape(dest)}" alt="{txt}" loading="lazy" referrerpolicy="no-referrer">')
            return guardar(f'<a href="{html.escape(dest)}" rel="noopener noreferrer" target="_blank">{txt}</a>')
        rel = buscar_nota(dest.split("#")[0].replace("%20", " "), desde)
        if not rel:
            return guardar(f'<span class="roto">{txt}</span>')
        if emb and Path(rel).suffix.lower() in IMAGEN:
            return guardar(f'<img class="incrustada" src="{html.escape(url("vault", rel, crudo="1"))}" alt="{txt}" loading="lazy">')
        return guardar(f'<a class="wiki" href="{html.escape(url("vault", rel))}">{txt}</a>')
    t = re.sub(r"(!?)\[([^\]]*)\]\(([^)\s]+)\)", md_link, t)
    t = re.sub(r"(?<![\"'=\w/])(https?://[^\s<)]+)", lambda m: guardar(f'<a href="{m.group(1)}" rel="noopener noreferrer" target="_blank">{m.group(1)}</a>'), t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<em>\1</em>", t)
    t = re.sub(r"~~(.+?)~~", r"<del>\1</del>", t)
    t = re.sub(r"==(.+?)==", r"<mark>\1</mark>", t)
    t = re.sub(r"(?<![\w&#/])#([\w/-]*[A-Za-zÀ-ÿ][\w/-]*)", r'<span class="etq">#\1</span>', t)
    return re.sub(r"\x00(\d+)\x00", lambda m: guard[int(m.group(1))], t)


def _frontmatter(lineas):
    if not lineas or lineas[0].strip() != "---":
        return "", lineas
    for i in range(1, min(len(lineas), 200)):
        if lineas[i].strip() == "---":
            filas = []
            for l in lineas[1:i]:
                m = re.match(r"^([\w -]+):\s*(.*)$", l)
                if m:
                    filas.append([m.group(1), m.group(2)])
                elif l.strip().startswith("- ") and filas:
                    filas[-1][1] += (", " if filas[-1][1] else "") + l.strip()[2:]
            cuerpo = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(v)}</td></tr>" for k, v in filas)
            return (f'<table class="fm">{cuerpo}</table>' if filas else ""), lineas[i + 1:]
    return "", lineas


def markdown(texto: str, desde: str = "") -> str:
    fm, lineas = _frontmatter(texto.splitlines())
    out, i = [fm], 0
    il = lambda s: _inline(s, desde)
    while i < len(lineas):
        l = lineas[i]
        s = l.strip()
        if not s:
            i += 1
            continue
        m = re.match(r"^(```+|~~~+)\s*(\S*)", s)
        if m:
            fin, bloque = m.group(1), []
            i += 1
            while i < len(lineas) and not lineas[i].strip().startswith(fin):
                bloque.append(lineas[i])
                i += 1
            i += 1
            out.append(f'<pre class="codigo"><code>{html.escape(chr(10).join(bloque))}</code></pre>')
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        if m:
            n = len(m.group(1))
            out.append(f"<h{n}>{il(m.group(2))}</h{n}>")
            i += 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            out.append("<hr>")
            i += 1
            continue
        if s.startswith(">"):
            bloque = []
            while i < len(lineas) and lineas[i].strip().startswith(">"):
                bloque.append(re.sub(r"^\s*>\s?", "", lineas[i]))
                i += 1
            m = re.match(r"^\[!(\w+)\][+-]?\s*(.*)$", bloque[0].strip()) if bloque else None
            if m:
                titulo = m.group(2) or m.group(1).capitalize()
                out.append(f'<div class="callout"><span class="etiqueta">{il(titulo)}</span>{markdown(chr(10).join(bloque[1:]), desde)}</div>')
            else:
                out.append(f"<blockquote>{markdown(chr(10).join(bloque), desde)}</blockquote>")
            continue
        if s.startswith("|") and i + 1 < len(lineas) and re.match(r"^\s*\|?\s*:?-{2,}", lineas[i + 1]):
            celdas = lambda x: [c.strip() for c in x.strip().strip("|").split("|")]
            cab = celdas(l)
            i += 2
            filas = []
            while i < len(lineas) and lineas[i].strip().startswith("|"):
                filas.append(celdas(lineas[i]))
                i += 1
            out.append('<div class="tabla"><table><tr>' + "".join(f"<th>{il(c)}</th>" for c in cab) + "</tr>"
                       + "".join("<tr>" + "".join(f"<td>{il(c)}</td>" for c in f) + "</tr>" for f in filas) + "</table></div>")
            continue
        if re.match(r"^\s*([-*+]|\d+[.)])\s+", l):
            items = []
            while i < len(lineas) and (re.match(r"^\s*([-*+]|\d+[.)])\s+", lineas[i]) or (lineas[i].startswith("  ") and lineas[i].strip() and items)):
                x = lineas[i]
                m = re.match(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$", x)
                if m:
                    items.append([len(m.group(1).expandtabs(4)) // 2, m.group(2)[0].isdigit(), m.group(3)])
                else:
                    items[-1][2] += " " + x.strip()
                i += 1
            out.append(_lista(items, il))
            continue
        parrafo = []
        while i < len(lineas) and lineas[i].strip() and not re.match(r"^\s*(#{1,6}\s|```|~~~|>|\||([-*+]|\d+[.)])\s)", lineas[i]):
            parrafo.append(lineas[i].strip())
            i += 1
        if not parrafo:
            parrafo.append(s)
            i += 1
        out.append("<p>" + "<br>".join(il(p) for p in parrafo) + "</p>")
    return "\n".join(out)


def _lista(items, il):
    out, pila = [], []
    for nivel, ordenada, texto in items:
        etiqueta = "ol" if ordenada else "ul"
        while pila and pila[-1][0] > nivel:
            out.append(f"</li></{pila.pop()[1]}>")
        if not pila or pila[-1][0] < nivel:
            out.append(f"<{etiqueta}>")
            pila.append((nivel, etiqueta))
        else:
            out.append("</li>")
        m = re.match(r"^\[([ xX/-])\]\s*(.*)$", texto)
        if m:
            hecho = m.group(1) != " "
            out.append(f'<li class="casilla{" hecho" if hecho else ""}"><span class="caja">{"✓" if hecho else ""}</span>{il(m.group(2))}')
        else:
            out.append(f"<li>{il(texto)}")
    while pila:
        out.append(f"</li></{pila.pop()[1]}>")
    return "".join(out).replace("<ul></li>", "<ul>").replace("<ol></li>", "<ol>")


# ---------- vistas ----------
def listar(raiz, real: Path, rel: str):
    try:
        entradas = list(os.scandir(real))
    except OSError as e:
        return None, f"No se puede leer esta carpeta ({e.strerror})."
    filas = []
    for e in entradas:
        if raiz == "vault" and e.name in OCULTAS_VAULT:
            continue
        p = Path(e.path)
        try:
            st = e.stat(follow_symlinks=False)
        except OSError:
            continue
        es_dir = e.is_dir(follow_symlinks=True) if not stat.S_ISLNK(st.st_mode) else Path(os.path.realpath(p)).is_dir()
        filas.append({"nombre": e.name, "dir": es_dir, "tam": st.st_size, "fecha": st.st_mtime,
                      "bloqueada": prohibida(Path(os.path.realpath(p)), raiz), "enlace": stat.S_ISLNK(st.st_mode),
                      "legible": os.access(p, os.R_OK | (os.X_OK if es_dir else 0))})
    filas.sort(key=lambda f: (not f["dir"], f["nombre"].lower()))
    return filas[:MAX_LISTA], ("" if len(filas) <= MAX_LISTA else f"Solo se muestran {MAX_LISTA} de {len(filas)}.")


def tabla_carpeta(raiz, rel, filas):
    out = []
    for f in filas:
        r2 = f"{rel}/{f['nombre']}".strip("/")
        nombre = html.escape(f["nombre"]) + ("/" if f["dir"] else "")
        if f["bloqueada"] or not f["legible"]:
            celda = f'<span class="secundario">{nombre}</span> <span class="mini secundario">{"bloqueada" if f["bloqueada"] else "sin permiso"}</span>'
        else:
            celda = f'<a class="{"carpeta" if f["dir"] else "archivo"}" href="{html.escape(url(raiz, r2))}">{nombre}</a>'
        out.append(f'<tr><td>{celda}{" <span class=\"mini secundario\">enlace</span>" if f["enlace"] else ""}</td>'
                   f'<td class="n">{"" if f["dir"] else tam(f["tam"])}</td><td class="n">{fecha(f["fecha"])}</td></tr>')
    if not out:
        return '<p class="vacio">Carpeta vacía.</p>'
    return '<div class="tabla"><table class="lista"><tr><th>Nombre</th><th class="n">Tamaño</th><th class="n">Modificado</th></tr>' + "".join(out) + "</table></div>"


def migas(raiz, rel):
    trozos = [t for t in rel.split("/") if t]
    out = [f'<a href="{html.escape(url(raiz))}">{"Vault" if raiz == "vault" else "/"}</a>']
    for k in range(len(trozos)):
        out.append(f'<a href="{html.escape(url(raiz, "/".join(trozos[:k + 1])))}">{html.escape(trozos[k])}</a>')
    return '<nav class="migas">' + '<span class="sep">/</span>'.join(out) + "</nav>"


def ver_archivo(raiz, real: Path, rel: str) -> str:
    st = real.stat()
    meta = f'<p class="meta mini secundario">{tam(st.st_size)} · {fecha(st.st_mtime)}</p>'
    suf = real.suffix.lower()
    crudo = html.escape(url(raiz, rel, crudo="1"))
    if suf in IMAGEN:
        return meta + f'<img class="incrustada" src="{crudo}" alt="">'
    if suf == ".pdf":
        return meta + f'<p><a class="boton" href="{crudo}" target="_blank" rel="noopener">Abrir PDF</a></p>'
    if st.st_size > MAX_TEXTO:
        return meta + f'<p class="vacio">Demasiado grande para mostrarlo ({tam(st.st_size)}).</p>'
    datos = real.read_bytes()
    if b"\0" in datos[:4096]:
        return meta + '<p class="vacio">Archivo binario. No se muestra.</p>'
    texto = datos.decode("utf-8", errors="replace")
    if suf == ".md":
        cuerpo = f'<article class="nota">{markdown(texto, rel if raiz == "vault" else "")}</article>'
        if raiz == "vault":
            atras = sorted(indice()["enlaces"].get(real.stem.lower(), set()) | indice()["enlaces"].get(real.name.lower(), set()))
            atras = [a for a in atras if a != rel]
            if atras:
                cuerpo += '<section class="atras"><span class="etiqueta">Enlazan aquí</span><ul>' + "".join(
                    f'<li><a class="wiki" href="{html.escape(url("vault", a))}">{html.escape(Path(a).stem)}</a> <span class="mini secundario">{html.escape(str(Path(a).parent))}</span></li>' for a in atras) + "</ul></section>"
        return meta + cuerpo
    return meta + f'<pre class="codigo"><code>{html.escape(texto)}</code></pre>'


def buscar(raiz, real: Path, rel: str, q: str):
    q = q.strip()
    if len(q) < 2:
        return '<p class="vacio">Escribe al menos 2 letras.</p>'
    ql, nombres, contenido, t0 = q.lower(), [], [], time.time()
    for r, dirs, archivos in os.walk(real):
        rp = Path(r)
        dirs[:] = sorted(d for d in dirs if not prohibida(rp / d, raiz) and d not in OCULTAS_VAULT and not (raiz == "vps" and d in ("proc", "sys")))
        for a in archivos:
            p = rp / a
            if prohibida(p, raiz):
                continue
            rr = str(p.relative_to(RAICES[raiz][1]))
            if ql in a.lower():
                nombres.append(rr)
            elif raiz == "vault" and a.endswith(".md") and len(contenido) < 100:
                try:
                    t = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                k = t.lower().find(ql)
                if k >= 0:
                    contenido.append((rr, t[max(0, k - 60):k + 80].replace("\n", " ")))
        if len(nombres) >= 200 or time.time() - t0 > 4:
            break
    li = lambda rr, extra="": f'<li><a class="archivo" href="{html.escape(url(raiz, rr))}">{html.escape(Path(rr).name)}</a> <span class="mini secundario">{html.escape(str(Path(rr).parent))}</span>{extra}</li>'
    out = f'<span class="etiqueta">En el nombre ({len(nombres)})</span><ul class="resultados">' + ("".join(li(r) for r in nombres) or "<li class=\"secundario\">-</li>") + "</ul>"
    if raiz == "vault":
        out += f'<span class="etiqueta">En el texto ({len(contenido)})</span><ul class="resultados">' + ("".join(li(r, f'<p class="mini secundario">{html.escape(x)}</p>') for r, x in contenido) or "<li class=\"secundario\">-</li>") + "</ul>"
    if time.time() - t0 > 4:
        out += '<p class="mini secundario">Búsqueda cortada a los 4 s. Busca desde una carpeta más concreta.</p>'
    return out


# ---------- página ----------
ESTILO = """<style>
.raices { display: flex; gap: 4px 28px; }
.migas { display: flex; flex-wrap: wrap; gap: 2px 6px; font-size: var(--t-sm); padding: 20px 0 8px; overflow-wrap: anywhere; }
.migas a { color: var(--tinta-2); text-decoration: none; } .migas a:last-child { color: var(--tinta-alta); } .migas a:hover { text-decoration: underline; }
.migas .sep { color: var(--linea-alta); }
.buscar { display: flex; gap: 8px; margin: 8px 0 16px; }
.buscar input { flex: 1; min-width: 0; font: inherit; font-size: 16px; padding: 8px 12px; color: var(--tinta-alta); background: var(--fondo-alto); border: 1px solid var(--linea); border-radius: 6px; }
table { width: 100%; border-collapse: collapse; font-size: var(--t-sm); }
th { text-align: left; font-weight: 500; color: var(--tinta-2); font-size: var(--t-xs); text-transform: uppercase; letter-spacing: .05em; padding: 8px 12px 8px 0; border-bottom: 1px solid var(--linea); }
td { padding: 7px 12px 7px 0; border-bottom: 1px solid var(--linea); vertical-align: top; overflow-wrap: anywhere; }
.n { text-align: right; font-family: var(--mono); font-weight: 300; white-space: nowrap; font-size: var(--t-xs); }
table.lista a { text-decoration: none; color: var(--tinta-alta); } table.lista a:hover { text-decoration: underline; }
table.lista a.carpeta { font-weight: 550; }
.meta { margin: 4px 0 20px; }
.nota { max-width: 76ch; line-height: 1.6; }
.nota h1, .nota h2, .nota h3 { font-family: var(--serif); font-weight: 400; line-height: 1.15; margin: 1.4em 0 .4em; color: var(--tinta-alta); }
.nota h1 { font-size: var(--t-3xl); } .nota h2 { font-size: var(--t-2xl); } .nota h3 { font-size: var(--t-xl); }
.nota h4, .nota h5, .nota h6 { font-size: var(--t-base); margin: 1.2em 0 .3em; }
.nota p, .nota ul, .nota ol, .nota blockquote, .nota .tabla, .nota pre { margin: .6em 0; }
.nota ul, .nota ol { padding-left: 1.4em; }
.nota li.casilla { list-style: none; margin-left: -1.4em; display: flex; gap: 8px; }
.nota .caja { flex: none; width: 15px; height: 15px; margin-top: .3em; border: 1px solid var(--tinta-2); border-radius: 3px; font-size: 11px; line-height: 13px; text-align: center; }
.nota li.hecho { color: var(--tinta-2); } .nota li.hecho .caja { background: var(--tinta-alta); color: var(--fondo); border-color: var(--tinta-alta); }
.nota a { color: var(--tinta-alta); text-underline-offset: 3px; }
.nota a.wiki { text-decoration-style: dotted; }
.nota .roto { color: var(--tinta-2); text-decoration: line-through dotted; }
.nota code, .codigo { font-family: var(--mono); font-size: .88em; }
.nota :not(pre) > code { background: var(--fondo-alto); padding: 1px 4px; border: 1px solid var(--linea); }
.codigo { background: var(--fondo-alto); border: 1px solid var(--linea); padding: 12px 14px; overflow-x: auto; white-space: pre; font-size: var(--t-xs); line-height: 1.55; max-width: 100%; }
.nota blockquote { border-left: 2px solid var(--linea-alta); padding-left: 14px; color: var(--tinta-2); }
.callout { border: 1px solid var(--linea); border-left: 3px solid var(--tinta-2); background: var(--fondo-alto); padding: 10px 14px; margin: .8em 0; }
.nota mark { background: var(--fondo-alto); color: var(--tinta-alta); box-shadow: inset 0 -.5em 0 var(--linea); }
.nota .etq { font-size: var(--t-sm); color: var(--tinta-2); border: 1px dashed var(--linea-alta); padding: 0 4px; }
.nota hr { border: 0; border-top: 1px solid var(--linea); margin: 1.6em 0; }
.fm { max-width: 76ch; margin-bottom: 8px; } .fm th { width: 1%; white-space: nowrap; text-transform: none; letter-spacing: 0; font-family: var(--mono); font-weight: 300; }
img.incrustada { max-width: 100%; height: auto; display: block; margin: .8em 0; border: 1px solid var(--linea); }
.atras { margin-top: 40px; padding-top: 16px; border-top: 1px solid var(--linea); max-width: 76ch; }
.atras ul, ul.resultados { list-style: none; padding: 0; margin: 8px 0 24px; } .atras li, ul.resultados li { padding: 5px 0; border-bottom: 1px solid var(--linea); }
ul.resultados a, .atras a { color: var(--tinta-alta); text-decoration: none; } ul.resultados a:hover { text-decoration: underline; }
.abrir { max-width: 362px; margin: 48px auto; }
.abrir p { font-size: var(--t-sm); color: var(--tinta-2); margin-bottom: 8px; }
.abrir input { width: 100%; font: inherit; font-size: 16px; padding: 9px 12px; color: var(--tinta-alta); background: var(--fondo-alto); border: 1px solid var(--linea); border-radius: 6px; margin: 6px 0 16px; }
</style>"""


def pagina(cuerpo: str, raiz: str, extra: str = "") -> str:
    titulo, icono, volver, volver_txt = TITULO[raiz]
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><title>{titulo}</title><link rel="stylesheet" href="/estatico/estilo.css">
<link rel="icon" href="/estatico/iconos/fav-{icono}.svg" type="image/svg+xml">{ESTILO}</head><body><div class="pagina">
<header class="cabecera"><div class="marca"><a class="volver" href="{volver}">← {volver_txt}</a><h1><span class="sello"><i class="ico ico-{icono}"></i></span>{titulo}</h1></div>
<div class="extra">{extra}</div></header>{cuerpo}</div></body></html>"""


def pagina_abrir(raiz, error="") -> str:
    nombre = "del Vault" if raiz == "vault" else "de los archivos del VPS"
    if not CLAVES[raiz].exists():
        return pagina(f'<div class="abrir"><p class="aviso">Falta crear la contraseña {nombre}. En el servidor: <code>ssh vps</code> y <code>/opt/portal/cambiar-clave-archivos.sh {raiz}</code>.</p></div>', raiz)
    err = f'<p class="error">{html.escape(error)}</p>' if error else ""
    return pagina(f"""<form class="abrir" method="post" action="{PREFIJO[raiz]}abrir">
<p>Tiene su propia contraseña, distinta de la del portal. Queda abierto 30 min desde el último uso.</p>{err}
<label class="campo"><span class="etiqueta">Contraseña {nombre}</span><input type="password" name="clave" autocomplete="off" required autofocus></label>
<button class="boton fuerte ancho">Abrir</button></form>""", raiz)


def comprobar(raiz: str, clave: str) -> bool:
    import bcrypt
    try:
        h = CLAVES[raiz].read_text().strip().encode()
    except OSError:
        return False
    return h.startswith(b"$2") and bcrypt.checkpw(clave.encode()[:72], h)


def responder(raiz: str, qs: dict):
    """GET del explorador ya abierto → (código, tipo, bytes, cabeceras extra)."""
    rel = qs.get("p", [""])[0]
    real, rel_o_error = resolver(raiz, rel)
    salir = f'<form method="post" action="{PREFIJO[raiz]}cerrar"><button class="boton">Cerrar</button></form>'
    if real is None:
        return 404, "text/html; charset=utf-8", pagina(f'<p class="vacio">{html.escape(rel_o_error)} <a class="enlace" href="{PREFIJO[raiz]}">Volver</a></p>', raiz, salir).encode(), {}
    rel = rel_o_error
    if "crudo" in qs:
        if not real.is_file() or real.stat().st_size > MAX_CRUDO:
            return 404, "text/plain; charset=utf-8", b"No disponible", {}
        tipo = mimetypes.guess_type(real.name)[0] or "application/octet-stream"
        if not (tipo.startswith("image/") or tipo == "application/pdf"):
            tipo = "text/plain; charset=utf-8"
        return 200, tipo, real.read_bytes(), {"Content-Security-Policy": "default-src 'none'; img-src data:; style-src 'unsafe-inline'; sandbox",
                                              "Content-Disposition": "inline"}
    q = qs.get("q", [""])[0]
    base_dir = real if real.is_dir() else real.parent
    rel_dir = rel if real.is_dir() else str(Path(rel).parent).strip(".")
    caja = (f'<form class="buscar" method="get" action="{PREFIJO[raiz]}">'
            f'<input type="hidden" name="p" value="{html.escape(rel_dir)}"><input name="q" value="{html.escape(q)}" placeholder="Buscar en esta carpeta" enterkeyhint="search">'
            f'<button class="boton">Buscar</button></form>')
    if q:
        cuerpo = migas(raiz, rel_dir) + caja + buscar(raiz, base_dir, rel_dir, q)
    elif real.is_dir():
        filas, nota = listar(raiz, real, rel)
        cuerpo = migas(raiz, rel) + caja + (f'<p class="vacio">{html.escape(nota)}</p>' if filas is None else tabla_carpeta(raiz, rel, filas) + (f'<p class="mini secundario">{nota}</p>' if nota else ""))
    else:
        try:
            cuerpo = migas(raiz, rel) + ver_archivo(raiz, real, rel)
        except OSError as e:
            cuerpo = migas(raiz, rel) + f'<p class="vacio">No se puede leer ({html.escape(e.strerror or "error")}).</p>'
    return 200, "text/html; charset=utf-8", pagina(cuerpo, raiz, salir).encode(), {}
