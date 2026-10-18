#!/usr/bin/env python3
"""Escribe en el vault la nota de To Do cuando se edita desde el portal (/tareas/).

El portal no puede escribir en el vault. Deja cada cambio como /var/lib/portal/todo-pendiente/<id>.json
({id, base, texto}: la nota entera ya editada) y todo-guardar.path lanza esto (root). Solo escribe esa
nota, y solo si nadie la ha cambiado desde que el portal la leyó (base = sha256 de la versión leída).
Resultado en /var/lib/portal/todo-resultado.json.
"""
import hashlib, json, os, pwd, shutil
from pathlib import Path

NOTA = Path("/srv/vault/30 - Personal/30 - Plan/To Do.md")
PENDIENTE = Path("/var/lib/portal/todo-pendiente")
RESULTADO = Path("/var/lib/portal/todo-resultado.json")


def resultado(pid, ok, error=""):
    tmp = RESULTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": pid, "ok": ok, "error": error}, ensure_ascii=False))
    u = pwd.getpwnam("portal")
    os.chown(tmp, u.pw_uid, u.pw_gid)
    os.chmod(tmp, 0o600)
    tmp.replace(RESULTADO)


def procesar(f: Path):
    pid = f.stem
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = str(d.get("id", pid))
        base, texto = d["base"], d["texto"]
        if not isinstance(texto, str) or len(texto) > 500_000:
            raise ValueError("petición no válida")
        viejo = NOTA.read_bytes()
        if hashlib.sha256(viejo).hexdigest() != base:
            raise ValueError("la nota ha cambiado en el vault; recarga")
        with open(NOTA, "w", encoding="utf-8", newline="") as w:  # en el sitio: conserva dueño (1000) y permisos
            w.write(texto)
        resultado(pid, True)
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"todo-guardar: error con {f.name}: {e}")
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json")):
        procesar(f)
    for f in PENDIENTE.iterdir():  # restos que no son peticiones
        if f.suffix != ".json":
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
