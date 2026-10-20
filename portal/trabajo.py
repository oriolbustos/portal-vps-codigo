#!/usr/bin/env python3
"""Panel Trabajo: el CV renderizado.

Los CV son LaTeX guardados como .md en el vault (CVS). Se compila el elegido con XeLaTeX
(fuente Carlito) y se convierte a PNG con pdftoppm para verlo también en el móvil.
Caché en /var/lib/portal/cv/, por huella del fuente: solo recompila si cambia. Solo lee del vault.
"""
import hashlib, html, json, os, re, secrets, shutil, subprocess, threading, time
from pathlib import Path
from urllib.parse import quote

CVS =Path("/srv/vault/20 - Career/40 - Opportunities/01 - CVs")
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
CACHE = ESTADO / "cv"
# Guardar: el portal no puede escribir en el vault (systemd lo deja en solo lectura). Deja la
# petición aquí y la recoge cv-guardar.path → /opt/portal/cv-guardar.py (root), que la escribe.
PENDIENTE = ESTADO / "cv-pendiente"
RESULTADO = ESTADO / "cv-resultado.json"
cerrojo = threading.Lock()


def lista() -> list:
    """CV en LaTeX, el más reciente primero (por la fecha AA-MM del nombre)."""
    out = []
    for p in CVS.glob("*.md"):
        try:
            if "\\documentclass" not in p.read_text(encoding="utf-8", errors="replace")[:4000]:
                continue
        except OSError:
            continue
        m = re.search(r"(\d{2})-(\d{2})", p.stem)
        out.append((m.group(1) + m.group(2) if m else "0000", p.stem, p))
    return [(nombre, p) for _, nombre, p in sorted(out, reverse=True)]


def compilar(p: Path) -> dict:
    return compilar_fuente(p.read_bytes())


def compilar_fuente(fuente: bytes) -> dict:
    """→ {"huella", "paginas", "error", "log"}. Deja cv.pdf y pagina-N.png en CACHE/huella/."""
    with cerrojo:  # una compilación a la vez: la vista previa puede llegar repetida
        return _compilar(fuente)


def _compilar(fuente: bytes) -> dict:
    huella = hashlib.sha256(fuente).hexdigest()[:16]
    d = CACHE / huella
    if (d / "cv.pdf").exists():
        return {"huella": huella, "paginas": len(list(d.glob("pagina-*.png"))), "error": "", "log": ""}
    CACHE.mkdir(parents=True, exist_ok=True)
    viejas = sorted((x for x in CACHE.iterdir() if x.is_dir() and x.name != "var"), key=lambda x: x.stat().st_mtime)
    for viejo in viejas[:-6]:  # guarda solo las 6 últimas compilaciones
        shutil.rmtree(viejo, ignore_errors=True)
    tmp = CACHE / (huella + ".tmp")
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir()
    (tmp / "cv.tex").write_bytes(fuente)
    entorno = dict(os.environ, HOME=str(CACHE / "var"), TEXMFVAR=str(CACHE / "var" / "texmf"),
                   XDG_CACHE_HOME=str(CACHE / "var" / "cache"))
    for motor in ("xelatex", "pdflatex"):  # las plantillas antiguas (\pdfglyphtounicode) solo van con pdflatex
        r = subprocess.run([motor, "-interaction=nonstopmode", "-halt-on-error", "-no-shell-escape", "cv.tex"],
                           cwd=tmp, env=entorno, capture_output=True, text=True, timeout=120)
        if r.returncode == 0 and (tmp / "cv.pdf").exists():
            break
    if r.returncode != 0 or not (tmp / "cv.pdf").exists():
        errores = [l for l in r.stdout.splitlines() if l.startswith("!")]
        log = "\n".join(r.stdout.splitlines()[-25:])
        shutil.rmtree(tmp, ignore_errors=True)
        return {"huella": huella, "paginas": 0, "error": errores[0] if errores else "No compila", "log": log}
    subprocess.run(["pdftoppm", "-png", "-r", "150", "cv.pdf", "pagina"], cwd=tmp, capture_output=True, timeout=60)
    for f in tmp.glob("pagina-*.png"):  # pdftoppm numera con ceros a la izquierda según el total
        f.rename(tmp / f"pagina-{int(f.stem.split('-')[1])}.png")
    for f in tmp.iterdir():
        if f.suffix not in (".pdf", ".png"):
            f.unlink()
    shutil.rmtree(d, ignore_errors=True)
    tmp.rename(d)
    return {"huella": huella, "paginas": len(list(d.glob("pagina-*.png"))), "error": "", "log": ""}


def archivo(huella: str, nombre: str):
    """Ruta segura a un PDF o PNG de la caché, o None."""
    if not re.fullmatch(r"[0-9a-f]{16}", huella) or not re.fullmatch(r"cv\.pdf|pagina-\d{1,2}\.png", nombre):
        return None
    f = CACHE / huella / nombre
    return f if f.is_file() else None


def pagina(elegido: str = "") -> str:
    cvs = lista()
    if not cvs:
        return '<p class="vacio">No encuentro ningún CV en LaTeX en el vault.</p>'
    nombre, p = next(((n, q) for n, q in cvs if n == elegido), cvs[0])
    try:
        c = compilar(p)
    except Exception as e:
        c = {"error": str(e), "log": "", "paginas": 0, "huella": ""}
    opciones = "".join(f'<option value="{html.escape(n)}"{" selected" if n == nombre else ""}>{html.escape(n)}</option>' for n, _ in cvs)
    pdf = ""
    if not c["error"]:
        pdf = f'<a class="boton" href="/trabajo/cv/{c["huella"]}/cv.pdf" download="{html.escape(nombre)}.pdf">Descargar PDF</a>'
    barra = f"""<div class="barra-cv"><form method="get"><label class="etiqueta" for="cv">Versión</label>
<select id="cv" name="cv" onchange="this.form.submit()">{opciones}</select></form>
<a class="boton" href="/trabajo/editar?cv={quote(nombre)}">Editar</a>{pdf}
<button class="boton fuerte crear" type="button" id="b-nuevo" aria-expanded="false" aria-controls="nuevo-cv">Crear CV nuevo</button></div>"""
    form = f"""<form class="nuevo-cv" id="nuevo-cv" hidden autocomplete="off">
<h2>Crear un CV nuevo</h2>
<p class="secundario peq">Se hace una copia de un CV que ya tienes y se abre en el editor para cambiarla. El original no se toca.</p>
<label class="paso"><span class="num">1</span><span class="etiqueta">De qué CV partes</span><select id="n-desde">{opciones}</select></label>
<label class="paso"><span class="num">2</span><span class="etiqueta">Cómo se llama el nuevo</span><input id="n-nombre" value="{html.escape(nombre_libre())}" maxlength="60" required>
<span class="mini secundario">Se guardará como <code>01 - CVs/<b id="n-vista">{html.escape(nombre_libre())}</b>.md</code></span></label>
<div class="fila-botones"><button class="boton fuerte">Crear copia y editarla</button><button class="boton" type="button" id="n-cancelar">Cancelar</button></div>
<p class="error" id="n-estado" role="status"></p></form>
<script>
(() => {{
  const $ = id => document.getElementById(id), f = $("nuevo-cv"), b = $("b-nuevo"), est = $("n-estado");
  const abrir = v => {{ f.hidden = !v; b.setAttribute("aria-expanded", v); if (v) {{ $("n-desde").focus(); f.scrollIntoView({{ block: "nearest", behavior: "smooth" }}); }} }};
  b.onclick = () => abrir(f.hidden); $("n-cancelar").onclick = () => abrir(false);
  $("n-nombre").oninput = e => {{ $("n-vista").textContent = e.target.value.trim().replace(/\\.md$/i, "") || "-"; }};
  f.onsubmit = async e => {{
    e.preventDefault(); est.className = "mini secundario"; est.textContent = "Creando la copia...";
    try {{
      const r = await fetch("/trabajo/nuevo", {{ method: "POST", headers: {{ "Content-Type": "application/json" }},
        body: JSON.stringify({{ texto: "", nombre: $("n-nombre").value, desde: $("n-desde").value }}) }});
      const d = await r.json();
      if (d.ok) location.href = "/trabajo/editar?cv=" + encodeURIComponent(d.cv);
      else {{ est.className = "error"; est.textContent = d.mensaje || "No se ha podido crear."; }}
    }} catch (err) {{ est.className = "error"; est.textContent = "No se ha podido crear: sin conexión."; }}
  }};
}})();
</script>"""
    cab = barra + form
    if c["error"]:
        return cab + f"""<div class="aviso">No compila: {html.escape(c['error'])}</div>
<details style="margin-top:12px"><summary>Ver el final del log</summary><pre class="mono mini">{html.escape(c['log'])}</pre></details>"""
    base = f"/trabajo/cv/{c['huella']}"
    imgs = "".join(f'<img src="{base}/pagina-{i}.png" alt="CV, página {i}" loading="lazy">' for i in range(1, c["paginas"] + 1))
    return cab + f"""<p class="mini secundario fuente">Fuente: <code>20 - Career/40 - Opportunities/01 - CVs/{html.escape(p.name)}</code></p>
<div class="hojas">{imgs}</div>"""


# ---------- edición ----------
def buscar(nombre: str):
    return next((p for n, p in lista() if n == nombre), None)


def sha(datos: bytes) -> str:
    return hashlib.sha256(datos).hexdigest()


def guardar(nombre: str, base: str, texto: str):
    """Pide a cv-guardar (root) que escriba el CV en el vault. → (código HTTP, {ok, mensaje, base})."""
    p = buscar(nombre)
    if not p:
        return 404, {"ok": False, "mensaje": "No encuentro ese CV en el vault."}
    texto = texto.replace("\r\n", "\n")
    if "\\documentclass" not in texto or len(texto) > 200_000:
        return 400, {"ok": False, "mensaje": "Eso no parece un CV en LaTeX (falta \\documentclass). No guardo."}
    actual = sha(p.read_bytes())
    if base != actual:
        return 409, {"ok": False, "mensaje": "El CV ha cambiado en el vault desde que lo abriste. Copia tus cambios y recarga."}
    nuevo = texto.encode("utf-8")
    if sha(nuevo) == actual:
        return 200, {"ok": True, "mensaje": "Sin cambios.", "base": actual}
    r = pedir({"archivo": p.name, "base": actual, "texto": texto})
    if r is None:
        return 504, {"ok": False, "mensaje": "El guardado no responde. Revisa cv-guardar en el servidor."}
    if r.get("ok"):
        return 200, {"ok": True, "mensaje": f"Guardado en el vault a las {time.strftime('%H:%M')}.", "base": sha(nuevo)}
    return 500, {"ok": False, "mensaje": "No se ha guardado: " + r.get("error", "error desconocido")}


def pedir(peticion: dict):
    """Deja la petición a cv-guardar (root) y espera su resultado. → dict o None si no responde."""
    pid = secrets.token_hex(8)
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"cv-{pid}.tmp"
    tmp.write_text(json.dumps(dict(peticion, id=pid), ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(40):  # cv-guardar.path lo recoge en menos de un segundo
        time.sleep(0.25)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return r
    return None


NOMBRE_CV = re.compile(r"[\w][\w .()-]{0,59}")


def nombre_libre() -> str:
    """Propuesta para un CV nuevo: CV-AA-MM, o CV-AA-MM-2... si ya existe."""
    base = time.strftime("CV-%y-%m")
    n, k = base, 2
    while (CVS / f"{n}.md").exists():
        n, k = f"{base}-{k}", k + 1
    return n


def crear(nombre: str, desde: str):
    """Copia un CV existente con otro nombre (vía cv-guardar). → (código HTTP, {ok, mensaje, cv})."""
    nombre = (nombre or "").strip()
    if nombre.lower().endswith(".md"):
        nombre = nombre[:-3].strip()
    if not NOMBRE_CV.fullmatch(nombre):
        return 400, {"ok": False, "mensaje": "Nombre no válido: letras, números, espacios, guiones o puntos, hasta 60."}
    if (CVS / f"{nombre}.md").exists():
        return 409, {"ok": False, "mensaje": f"Ya existe {nombre}.md. Elige otro nombre."}
    origen = buscar(desde)
    if not origen:
        return 404, {"ok": False, "mensaje": "No encuentro el CV de partida."}
    texto = origen.read_text(encoding="utf-8", errors="replace")
    r = pedir({"archivo": f"{nombre}.md", "base": "", "texto": texto, "nuevo": True})
    if r is None:
        return 504, {"ok": False, "mensaje": "La creación no responde. Revisa cv-guardar en el servidor."}
    if not r.get("ok"):
        return 500, {"ok": False, "mensaje": "No se ha creado: " + r.get("error", "error desconocido")}
    return 200, {"ok": True, "mensaje": f"Creado {nombre} a partir de {desde}.", "cv": nombre}


def editor(nombre: str) -> str:
    p = buscar(nombre)
    if not p:
        return '<p class="vacio">No encuentro ese CV. <a class="enlace" href="/trabajo/">Volver</a></p>'
    datos = p.read_bytes()
    texto = datos.decode("utf-8", errors="replace")
    ini = json.dumps({"cv": nombre, "base": sha(datos)}).replace("</", "<\\/")
    return f"""<div class="barra-cv"><div><span class="etiqueta">Editando</span> <b>{html.escape(nombre)}</b>
<span class="mini secundario" id="estado"></span></div>
<div class="fila-botones"><a class="boton" href="/trabajo/?cv={quote(nombre)}">Salir</a>
<button class="boton" id="b-previa" title="Ctrl+Enter">Vista previa</button>
<button class="boton fuerte" id="b-guardar" title="Ctrl+S">Guardar en el vault</button></div></div>
<div class="editor">
<textarea id="fuente" spellcheck="false" autocapitalize="off" autocomplete="off">{html.escape(texto)}</textarea>
<div class="previa" id="previa"><p class="vacio">Pulsa Vista previa para compilar.</p></div>
</div>
<script id="ini" type="application/json">{ini}</script>
<script>
"use strict";
const INI = JSON.parse(document.getElementById("ini").textContent);
const ta = document.getElementById("fuente"), estado = document.getElementById("estado"), previa = document.getElementById("previa");
let base = INI.base, sucio = false, ocupado = false;
const decir = t => {{ estado.textContent = t; }};
ta.addEventListener("input", () => {{ if (!sucio) {{ sucio = true; decir("Cambios sin guardar"); }} }});
addEventListener("beforeunload", e => {{ if (sucio) {{ e.preventDefault(); e.returnValue = ""; }} }});
async function post(url, cuerpo) {{
  const r = await fetch(url, {{ method: "POST", headers: {{ "Content-Type": "application/json" }}, body: JSON.stringify(cuerpo) }});
  let d = {{}}; try {{ d = await r.json(); }} catch (e) {{ d = {{ mensaje: "Respuesta rara del servidor (" + r.status + ")." }}; }}
  return d;
}}
async function vistaPrevia() {{
  if (ocupado) return; ocupado = true; decir("Compilando...");
  try {{
    const d = await post("/trabajo/compilar", {{ texto: ta.value }});
    previa.replaceChildren();
    if (d.error || !d.huella) {{
      const a = document.createElement("div"); a.className = "aviso"; a.textContent = d.error ? "No compila: " + d.error : (d.mensaje || "No se ha podido compilar.");
      const pre = document.createElement("pre"); pre.className = "mono mini"; pre.textContent = d.log || "";
      previa.append(a, pre); decir("No compila");
    }} else {{
      for (let i = 1; i <= d.paginas; i++) {{ const im = document.createElement("img"); im.src = `/trabajo/cv/${{d.huella}}/pagina-${{i}}.png`; im.alt = "Página " + i; previa.append(im); }}
      decir(sucio ? "Vista previa al día, cambios sin guardar" : "Vista previa al día");
    }}
  }} catch (e) {{ decir("No se ha podido compilar"); }}
  ocupado = false;
}}
async function guardarCV() {{
  if (ocupado) return; ocupado = true; decir("Guardando...");
  try {{
    const d = await post("/trabajo/guardar", {{ cv: INI.cv, base, texto: ta.value }});
    if (d.ok) {{ base = d.base; sucio = false; }}
    decir(d.mensaje || "Error al guardar");
  }} catch (e) {{ decir("No se ha guardado: sin conexión"); }}
  ocupado = false;
}}
document.getElementById("b-previa").onclick = vistaPrevia;
document.getElementById("b-guardar").onclick = guardarCV;
addEventListener("keydown", e => {{
  if ((e.ctrlKey || e.metaKey) && e.key === "s") {{ e.preventDefault(); guardarCV(); }}
  if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {{ e.preventDefault(); vistaPrevia(); }}
}});
vistaPrevia();
</script>"""
