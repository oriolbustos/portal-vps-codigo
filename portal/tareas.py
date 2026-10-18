#!/usr/bin/env python3
"""Panel Tareas: lee las listas de cada ámbito del vault y las ordena en una matriz de Eisenhower.

Fuente (solo lectura): "30 - Personal/30 - Plan/Panel.md" da los ámbitos ([[Identity]], [[Work]]...);
de cada nota de ámbito se lee la sección "## Plan": "### Doing", "### To Do" y "### Monitoring",
con una tarea por viñeta. Las viñetas con sangría o que empiezan por "- - " son subtareas.

La clasificación urgente / importante se guarda en /var/lib/portal/tareas.json por huella de
ámbito + texto (si cambias el texto en el vault, la tarea vuelve a "sin clasificar"). Las etiquetas
#urgente y #importante escritas en el vault mandan sobre lo guardado.
"""
import hashlib, json, os, re, threading, time
from pathlib import Path

PLAN = Path("/srv/vault/30 - Personal/30 - Plan")
PANEL = PLAN / "Panel.md"
GUARDADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal")) / "tareas.json"
ESTADOS = {"doing": "doing", "to do": "todo", "todo": "todo", "monitoring": "seguimiento", "monitorizando": "seguimiento"}
cerrojo = threading.Lock()


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
        ruta = next(iter(sorted((PLAN / "Ámbitos").rglob(f"{n}.md"))), None)
        if ruta:
            out.append((n, ruta))
    return out


def limpiar(t: str) -> str:
    t = re.sub(r"^\[[ xX/-]\]\s*", "", t.strip())
    t = re.sub(r"\[\[(?:[^\]|]+\|)?([^\]]+)\]\]", r"\1", t)
    return t.strip()


def huella(ambito: str, texto: str) -> str:
    return hashlib.sha1(f"{ambito}|{texto.lower().strip()}".encode()).hexdigest()[:12]


def leer_ambito(nombre: str, ruta: Path) -> list:
    tareas, estado, en_plan, actual = [], None, False, None
    for linea in ruta.read_text(encoding="utf-8", errors="replace").splitlines():
        if linea.startswith("## "):
            en_plan = linea[3:].strip().lower() == "plan"
            estado, actual = None, None
            continue
        if not en_plan:
            continue
        if linea.startswith("### "):
            estado, actual = ESTADOS.get(linea[4:].strip().lower()), None
            continue
        m = re.match(r"^(\s*)-\s+(.*)$", linea)
        if not m or not estado:
            continue
        sangria, texto = m.group(1), m.group(2)
        hija = bool(sangria) or texto.startswith("- ")
        texto = limpiar(re.sub(r"^-\s+", "", texto))
        if not texto or re.match(r"^\[[xX]\]", m.group(2)):
            continue
        if hija and actual:
            actual["sub"].append(texto)
            continue
        etiquetas = set(re.findall(r"#(urgente|importante)\b", texto, re.I))
        visible = re.sub(r"\s*#(urgente|importante)\b", "", texto, flags=re.I).strip()
        actual = {"id": huella(nombre, visible), "texto": visible, "ambito": nombre, "estado": estado, "sub": [],
                  "tag_u": "urgente" in {e.lower() for e in etiquetas}, "tag_i": "importante" in {e.lower() for e in etiquetas}}
        tareas.append(actual)
    return tareas


def cargar() -> dict:
    try:
        return json.loads(GUARDADO.read_text())
    except (OSError, ValueError):
        return {}


def todas() -> dict:
    guardado = cargar()
    lista, avisos, nombres = [], [], []
    for nombre, ruta in ambitos():
        nombres.append(nombre)
        try:
            lista += leer_ambito(nombre, ruta)
        except Exception as e:  # una nota rota no tumba el panel
            avisos.append(f"{nombre}: {e}")
    for t in lista:
        g = guardado.get(t["id"])
        t["u"] = True if t["tag_u"] else (g["u"] if g else None)
        t["i"] = True if t["tag_i"] else (g["i"] if g else None)
        if (t["tag_u"] or t["tag_i"]) and not g:
            t["u"], t["i"] = bool(t["tag_u"]), bool(t["tag_i"])
    return {"ambitos": nombres, "tareas": lista, "avisos": avisos, "generado": time.strftime("%Y-%m-%d %H:%M")}


def clasificar(id_: str, u, i) -> bool:
    """u, i: True/False, o ambos None para dejarla sin clasificar."""
    if not re.fullmatch(r"[0-9a-f]{12}", id_ or ""):
        return False
    t = next((t for t in todas()["tareas"] if t["id"] == id_), None)
    if not t:
        return False
    with cerrojo:
        g = cargar()
        if u is None and i is None:
            g.pop(id_, None)
        else:
            g[id_] = {"u": bool(u), "i": bool(i), "texto": t["texto"], "ambito": t["ambito"], "cuando": time.strftime("%Y-%m-%d %H:%M")}
        tmp = GUARDADO.with_suffix(".tmp")
        tmp.write_text(json.dumps(g, ensure_ascii=False, indent=1))
        os.chmod(tmp, 0o600)
        tmp.replace(GUARDADO)
    return True


def html(plantilla: Path) -> str:
    js = json.dumps(todas(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)


if __name__ == "__main__":
    print(json.dumps(todas(), ensure_ascii=False, indent=1))
