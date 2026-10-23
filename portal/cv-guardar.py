#!/usr/bin/env python3
"""Escribe en el vault los CV que se editan desde el portal (/trabajo/editar).

El portal no puede escribir en el vault (systemd lo deja en solo lectura). Deja cada guardado como
/var/lib/portal/cv-pendiente/<id>.json ({id, archivo, base, texto}) y cv-guardar.path lanza esto (root).
Solo sobrescribe un CV que ya existe en la carpeta de CVs, y solo si nadie lo ha cambiado desde que
se abrió el editor (base = sha256 del original). Con "nuevo": true crea un CV que no existe (copia hecha
desde /trabajo/, botón Nuevo): nunca sobrescribe, y lo deja con dueño 1000 como el resto del vault.
Resultado en /var/lib/portal/cv-resultado.json, que el portal espera para responder.
"""
import hashlib, json, os, pwd, re, shutil, time
from pathlib import Path

CVS = Path("/srv/vault/20 - Career/Opportunities/CVs")
PENDIENTE = Path("/var/lib/portal/cv-pendiente")
RESULTADO = Path("/var/lib/portal/cv-resultado.json")


def resultado(pid, ok, error=""):
    tmp = RESULTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": pid, "ok": ok, "error": error, "cuando": time.strftime("%Y-%m-%d %H:%M:%S")}, ensure_ascii=False))
    u = pwd.getpwnam("portal")
    os.chown(tmp, u.pw_uid, u.pw_gid)
    os.chmod(tmp, 0o600)
    tmp.replace(RESULTADO)


def procesar(f: Path):
    pid = f.stem
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = str(d.get("id", pid))
        archivo, base, texto = d["archivo"], d["base"], d["texto"]
        if not re.fullmatch(r"[\w .-]+\.md", archivo) or "\\documentclass" not in texto or len(texto) > 200_000:
            raise ValueError("petición no válida")
        destino = CVS / archivo
        if d.get("nuevo") is True:
            if destino.resolve().parent != CVS.resolve() or destino.exists():
                raise ValueError("ya existe un archivo con ese nombre")
            with open(destino, "x", encoding="utf-8") as w:  # "x": falla si alguien lo creó entre medias
                w.write(texto)
            os.chown(destino, 1000, 1000)
            os.chmod(destino, 0o644)
            resultado(pid, True)
            print(f"cv-guardar: {archivo} creado")
            return
        if destino.resolve().parent != CVS.resolve() or not destino.is_file():
            raise ValueError("ese CV no existe en el vault")
        viejo = destino.read_bytes()
        if hashlib.sha256(viejo).hexdigest() != base:
            raise ValueError("el CV ha cambiado en el vault desde que se abrió el editor")
        with open(destino, "w", encoding="utf-8") as w:  # en el sitio: conserva dueño (1000) y permisos
            w.write(texto)
        resultado(pid, True)
        print(f"cv-guardar: {archivo} guardado")
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"cv-guardar: error con {f.name}: {e}")
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json")):
        procesar(f)
    for f in PENDIENTE.iterdir():  # restos que no son peticiones
        if f.suffix != ".json":
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
