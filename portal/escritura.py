"""Reglas de escritura editables desde /config/ (pestaña Escritura).

Son la sección "## Forma de escribir" de la nota del vault estilo-frontend.md (la misma que lee la IA y la
guía de estilo). Cada "### Apartado" con sus viñetas "- regla". Lo que no es viñeta se conserva tal cual.
El portal no escribe en el vault: deja la nota entera en /var/lib/portal/estilo-pendiente/ y
estilo-guardar.path lanza /opt/portal/estilo-guardar.py (root), que solo escribe esa nota y solo si no
cambió desde que se leyó. De fábrica = la sección guardada en /opt/portal/estilo-fabrica.md.
"""
import hashlib, json, os, secrets, time
from pathlib import Path

import rutas

NOTA = rutas.ESTILO
FABRICA = Path(__file__).parent / "estilo-fabrica.md"
SECCION = "Forma de escribir"
ESTADO = Path(os.environ.get("STATE_DIRECTORY", "/var/lib/portal"))
PENDIENTE = ESTADO / "estilo-pendiente"
RESULTADO = ESTADO / "estilo-resultado.json"


def _partir(texto: str):
    """→ (antes, líneas de la sección sin su título, después). Sin la sección: se añade al final."""
    lineas = texto.split("\n")
    ini = next((i for i, l in enumerate(lineas) if l.strip() == f"## {SECCION}"), None)
    if ini is None:
        return texto.rstrip("\n") + f"\n\n## {SECCION}\n", [], ""
    fin = next((i for i in range(ini + 1, len(lineas)) if lineas[i].startswith("## ")), len(lineas))
    return "\n".join(lineas[:ini + 1]) + "\n", lineas[ini + 1:fin], ("\n".join(lineas[fin:]) if fin < len(lineas) else "")


def apartados(lineas: list) -> list:
    """Líneas de la sección → [{titulo, reglas: [texto], otros: [línea]}] (el primero puede no tener título)."""
    out = [{"titulo": "", "reglas": [], "otros": []}]
    for l in lineas:
        if l.startswith("### "):
            out.append({"titulo": l[4:].strip(), "reglas": [], "otros": []})
        elif l.startswith("- "):
            out[-1]["reglas"].append(l[2:].strip())
        elif l.startswith(("  ", "\t")) and l.strip() and out[-1]["reglas"]:
            out[-1]["reglas"][-1] += " " + l.strip()   # continuación de la viñeta
        elif l.strip():
            out[-1]["otros"].append(l)
    return [a for a in out if a["titulo"] or a["reglas"] or a["otros"]]


def componer(aps: list) -> list:
    out = [""]
    for a in aps:
        if a.get("titulo"):
            out.append(f"### {a['titulo']}")
        out += list(a.get("otros", []))
        out += [f"- {r}" for r in a.get("reglas", [])]
        out.append("")
    return out


def leer() -> dict:
    texto = NOTA.read_text(encoding="utf-8")
    return {"base": hashlib.sha256(texto.encode()).hexdigest(), "apartados": apartados(_partir(texto)[1])}


def validar(aps) -> list:
    if not isinstance(aps, list) or len(aps) > 40:
        raise ValueError("Petición no válida.")
    limpio = []
    for a in aps:
        if not isinstance(a, dict):
            raise ValueError("Petición no válida.")
        titulo = " ".join(str(a.get("titulo", "")).split())[:80]
        reglas = [" ".join(str(r).split())[:600] for r in a.get("reglas", []) if str(r).strip()][:80]
        otros = [str(o).rstrip()[:600] for o in a.get("otros", []) if str(o).strip() and not str(o).startswith("#")][:20]
        if titulo or reglas or otros:
            limpio.append({"titulo": titulo, "reglas": reglas, "otros": otros})
    return limpio


def guardar(base: str, aps: list):
    """→ (ok, mensaje). aps = apartados nuevos; None = volver a los de fábrica."""
    texto = NOTA.read_text(encoding="utf-8")
    if hashlib.sha256(texto.encode()).hexdigest() != base:
        return False, "La nota ha cambiado en el vault. Recargo."
    if aps is None:
        aps = apartados(FABRICA.read_text(encoding="utf-8").split("\n"))
    antes, _, despues = _partir(texto)
    nuevo = antes + "\n".join(componer(validar(aps))).rstrip("\n") + "\n" + ("\n" + despues if despues else "")
    pid = secrets.token_hex(8)
    PENDIENTE.mkdir(exist_ok=True)
    tmp = ESTADO / f"estilo-{pid}.tmp"
    tmp.write_text(json.dumps({"id": pid, "base": base, "texto": nuevo}, ensure_ascii=False), encoding="utf-8")
    tmp.replace(PENDIENTE / f"{pid}.json")
    for _ in range(50):  # estilo-guardar.path lo recoge en menos de un segundo
        time.sleep(0.1)
        try:
            r = json.loads(RESULTADO.read_text())
        except (OSError, ValueError):
            continue
        if r.get("id") == pid:
            return bool(r.get("ok")), r.get("error", "")
    return False, "El guardado no responde. Prueba otra vez en un momento."
