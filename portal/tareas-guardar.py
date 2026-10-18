#!/usr/bin/env python3
"""Escribe en el vault una nota de ámbito cuando se edita desde el portal (/tareas/).

El portal no puede escribir en el vault. Deja cada cambio como /var/lib/portal/tareas-pendiente/<id>.json
({id, ruta, base, texto}: ruta relativa a "30 - Plan/Ámbitos", texto = la nota entera ya editada) y
tareas-guardar.path lanza esto (root). Solo escribe notas .md que ya existen dentro de Ámbitos, y solo si
nadie las ha cambiado desde que el portal las leyó (base = sha256). Resultado en /var/lib/portal/tareas-resultado.json.
"""
import hashlib, json, os, pwd, shutil
from pathlib import Path

AMBITOS = Path("/srv/vault/30 - Personal/30 - Plan/Ámbitos")
PENDIENTE = Path("/var/lib/portal/tareas-pendiente")
RESULTADO = Path("/var/lib/portal/tareas-resultado.json")


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
        base, texto, rel = d["base"], d["texto"], d["ruta"]
        if not isinstance(texto, str) or len(texto) > 500_000 or not isinstance(rel, str):
            raise ValueError("petición no válida")
        nota = AMBITOS / rel
        if nota.suffix != ".md" or nota.is_symlink() or not nota.is_file() or not nota.resolve().is_relative_to(AMBITOS.resolve()):
            raise ValueError("esa nota no es de Ámbitos")
        if hashlib.sha256(nota.read_bytes()).hexdigest() != base:
            raise ValueError("la nota ha cambiado en el vault; recarga")
        with open(nota, "w", encoding="utf-8", newline="") as w:  # en el sitio: conserva dueño (1000) y permisos
            w.write(texto)
        resultado(pid, True)
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"tareas-guardar: error con {f.name}: {e}")
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json")):
        procesar(f)
    for f in PENDIENTE.iterdir():  # restos que no son peticiones
        if f.suffix != ".json":
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
