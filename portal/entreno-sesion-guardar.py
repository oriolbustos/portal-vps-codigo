#!/usr/bin/env python3
"""Escribe en el vault la nota diaria de entreno cuando se apunta una serie desde el portal (/entreno/sesion/).

El portal no puede escribir en el vault. Deja cada cambio como /var/lib/portal/entreno-sesion-pendiente/<id>.json
({id, fecha, base, crear, texto}: texto = la nota entera con la tabla "Entreno" ya reescrita) y
entreno-sesion-guardar.path lanza esto (root). Solo escribe 40 - Entreno/20-diario/AAAA/MM/AAAA-MM-DD.md:
- si existe, solo si nadie la ha cambiado desde que el portal la leyó (base = sha256), en el sitio (conserva dueño);
- si no existe y crear = true, la crea (y la carpeta del mes) con dueño 1000. Nunca pisa una nota que ya exista.
Resultado en /var/lib/portal/entreno-sesion-resultado.json.
"""
import hashlib, json, os, pwd, re, shutil, sys
from pathlib import Path

DIARIO = Path(os.environ.get("ENTRENO_DIARIO", "/srv/vault/40 - Entreno/20-diario"))
ESTADO = Path(os.environ.get("ENTRENO_ESTADO", "/var/lib/portal"))
PENDIENTE = ESTADO / "entreno-sesion-pendiente"
RESULTADO = ESTADO / "entreno-sesion-resultado.json"
DUENO = 1000


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
        fecha, base, texto, crear = d["fecha"], d["base"], d["texto"], d.get("crear") is True
        if not isinstance(texto, str) or len(texto) > 300_000 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fecha)):
            raise ValueError("petición no válida")
        mes = DIARIO / fecha[:4] / fecha[5:7]
        nota = mes / f"{fecha}.md"
        if not nota.resolve().is_relative_to(DIARIO.resolve()) or nota.is_symlink():
            raise ValueError("esa nota no es del diario de entreno")
        if crear:
            for p in (mes.parent, mes):
                if not p.exists():
                    p.mkdir()
                    os.chown(p, DUENO, DUENO)
            try:
                fd = os.open(nota, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)  # nunca pisa una que ya exista
            except FileExistsError:
                raise ValueError("la nota de hoy ya existe; recarga")
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as w:
                w.write(texto)
            os.chown(nota, DUENO, DUENO)
        else:
            if not nota.is_file():
                raise ValueError("la nota de hoy ya no está; recarga")
            if hashlib.sha256(nota.read_bytes()).hexdigest() != base:
                raise ValueError("la nota ha cambiado en el vault; recarga")
            with open(nota, "w", encoding="utf-8", newline="") as w:  # en el sitio: conserva dueño (1000) y permisos
                w.write(texto)
        resultado(pid, True)
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"entreno-sesion-guardar: error con {f.name}: {e}", file=sys.stderr)
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json")):
        procesar(f)
    for f in PENDIENTE.iterdir():  # restos que no son peticiones
        if f.suffix != ".json":
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
