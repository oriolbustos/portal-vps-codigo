#!/usr/bin/env python3
"""Panel Listas: las notas de "30 - Personal/10 - Listas/" (compra, regalos, deseos, ver, viajes).

Lee cada nota (solo las de la carpeta, no las de iPhone/ ni Mac/). Dos formatos:
- con casillas ("- [ ] Pan", "- [x] Pan ✅ 2026-05-19"): se marca y desmarca como en Obsidian;
- de líneas sueltas ("silla de calidad"), con "- " debajo como detalle: se archiva (va a "## Archivado").
Las secciones son los títulos "## " y "### ".

Escribir: el portal no puede tocar el vault. Deja la operación en /var/lib/portal/listas-pendiente/ y
listas-guardar.path → /opt/portal/listas-guardar.py (root) la aplica línea a línea sobre la nota actual.
"""
import hashlib, json, os, re, secrets, time
from pathlib import Path

CARPETA = Path("/srv/vault/30 - Personal/10 - Listas")
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "listas-pendiente"
RESULTADO = ESTADO / "listas-resultado.json"
NOMBRES = {"Lista de la compra": "Compra", "presentlist": "Regalos", "wishlist": "Deseos",
           "watchlist": "Ver y leer", "triplist": "Viajes"}
ORDEN = ["Lista de la compra", "presentlist", "wishlist", "watchlist", "triplist"]
CASILLA = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
ARCHIVO = "Archivado"


def notas() -> list:
    ps = [p for p in CARPETA.glob("*.md") if p.is_file() and not p.name.startswith("_")]
    return sorted(ps, key=lambda p: (ORDEN.index(p.stem) if p.stem in ORDEN else 99, p.stem.lower()))


def leer(p: Path) -> dict:
    """→ {archivo, nombre, casillas, secciones: [{titulo, items: [{n, linea, texto, hecho, cuando, sub}]}]}."""
    lineas = p.read_text(encoding="utf-8", errors="replace").splitlines()
    casillas = sum(bool(CASILLA.match(l)) for l in lineas) >= max(1, len([l for l in lineas if l.strip()]) // 2)
    secciones = [{"titulo": "", "items": []}]
    ultimo = None
    for n, l in enumerate(lineas):
        t = l.rstrip()
        if not t.strip() or re.fullmatch(r"\s*-{3,}\s*", t):
            continue
        m = re.match(r"^#{1,6}\s+(.*)$", t)
        if m:
            secciones.append({"titulo": m.group(1).strip(), "items": []})
            ultimo = None
            continue
        c = CASILLA.match(t)
        if c:
            texto = c.group(3).strip()
            cuando = ""
            f = re.search(r"\s*✅\s*(\d{4}-\d{2}-\d{2})\s*$", texto)
            if f:
                cuando, texto = f.group(1), texto[:f.start()].strip()
            ultimo = {"n": n, "linea": l, "texto": texto, "hecho": c.group(2) != " ", "cuando": cuando, "sub": []}
            secciones[-1]["items"].append(ultimo)
            continue
        d = re.match(r"^\s*-\s+(.*)$", t)
        if d and ultimo is not None and not casillas:
            ultimo["sub"].append(d.group(1).strip())
            continue
        texto = d.group(1).strip() if d else t.strip()
        ultimo = {"n": n, "linea": l, "texto": texto, "hecho": False, "cuando": "", "sub": []}
        secciones[-1]["items"].append(ultimo)
    secciones = [s for s in secciones if s["items"] or s["titulo"]]
    return {"archivo": p.name, "nombre": NOMBRES.get(p.stem, p.stem), "casillas": casillas, "secciones": secciones,
            "huella": hashlib.sha256("\n".join(lineas).encode()).hexdigest()[:12]}


def todas() -> list:
    out = []
    for p in notas():
        try:
            out.append(leer(p))
        except Exception as e:  # una nota rota no tumba el panel
            out.append({"archivo": p.name, "nombre": NOMBRES.get(p.stem, p.stem), "casillas": False, "secciones": [], "error": str(e)})
    return out


def operar(d: dict):
    """d = {op: anadir|marcar|archivar, archivo, seccion?, texto?, n?, linea?, hecho?} → (código, respuesta)."""
    op, archivo = d.get("op"), str(d.get("archivo", ""))
    if op not in ("anadir", "marcar", "archivar") or archivo not in {p.name for p in notas()}:
        return 400, {"ok": False, "mensaje": "Petición no válida."}
    if op == "anadir":
        texto = re.sub(r"\s+", " ", str(d.get("texto", ""))).strip()
        if not texto or len(texto) > 300:
            return 400, {"ok": False, "mensaje": "Escribe algo (máximo 300 caracteres)."}
        d = {"op": op, "archivo": archivo, "seccion": str(d.get("seccion", ""))[:200], "texto": texto}
    else:
        if not isinstance(d.get("n"), int) or not isinstance(d.get("linea"), str):
            return 400, {"ok": False, "mensaje": "Petición no válida."}
        d = {"op": op, "archivo": archivo, "n": d["n"], "linea": d["linea"], "hecho": bool(d.get("hecho"))}
    pid = secrets.token_hex(8)
    d["id"] = pid
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"lista-{pid}.tmp"
    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(40):  # listas-guardar.path lo recoge en menos de un segundo
        time.sleep(0.2)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            lista = leer(CARPETA / archivo)
            if r.get("ok"):
                return 200, {"ok": True, "lista": lista}
            return 409, {"ok": False, "mensaje": r.get("error", "No se ha guardado."), "lista": lista}
    return 504, {"ok": False, "mensaje": "El guardado no responde. Revisa listas-guardar en el servidor."}


def html(plantilla: Path) -> str:
    js = json.dumps({"listas": todas(), "generado": time.strftime("%H:%M")}, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)
