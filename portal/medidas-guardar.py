#!/usr/bin/env python3
"""Guarda lo que llega de las secciones Perímetros y Fotos del panel Entreno del portal.

El portal no puede escribir en el vault ni en /srv. Deja cada petición en /var/lib/portal/medidas-pendiente/<id>.json
y medidas-guardar.path lanza esto (root). Resultado en /var/lib/portal/medidas-resultado.json.
- {op: checkin, fecha, base, texto}: escribe 30 - Health/checkins/<fecha>.md. base = sha256 de la nota que leyó
  el portal; "" = nota nueva (solo si no existe). Escribe en el sitio para conservar el dueño (1000).
- {op: foto, fecha, pose} + <id>.jpg: la deja en /srv/entreno-fotos/<fecha>/<pose>.jpg (root:portal, 640).
  Si ya había una, la vieja se queda como <pose>-antes-AAAAMMDD-HHMMSS.jpg: nunca se borra nada.
- {op: mover, fecha, pose, a_fecha, a_pose}: cambia la foto de fecha o de pose. Si el destino ya tiene foto,
  las dos se intercambian.
- {op: eliminar, fecha, pose}: la mueve a /srv/entreno-fotos/.eliminadas/<fecha>-<pose>-AAAAMMDD-HHMMSS.jpg
  (no sale en el panel; se recupera a mano).
"""
import hashlib, json, os, pwd, re, shutil, sys, time
from datetime import datetime
from pathlib import Path

CHECKINS = Path(os.environ.get("MEDIDAS_CHECKINS", "/srv/vault/30 - Health/checkins"))
FOTOS = Path(os.environ.get("MEDIDAS_FOTOS", "/srv/entreno-fotos"))
PENDIENTE = Path(os.environ.get("MEDIDAS_PENDIENTE", "/var/lib/portal/medidas-pendiente"))
RESULTADO = PENDIENTE.parent / "medidas-resultado.json"
POSES = {"frente", "lado-izq", "lado-der", "espalda"}
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")
VAULT_UID = 1000


def portal():
    u = pwd.getpwnam("portal")
    return u.pw_uid, u.pw_gid


def resultado(pid, ok, error=""):
    tmp = RESULTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": pid, "ok": ok, "error": error}, ensure_ascii=False))
    os.chown(tmp, *portal())
    os.chmod(tmp, 0o600)
    tmp.replace(RESULTADO)


def checkin(d):
    fecha, base, texto = d["fecha"], d["base"], d["texto"]
    if not FECHA.match(fecha) or not isinstance(texto, str) or len(texto) > 100_000 or not texto.startswith("---"):
        raise ValueError("petición no válida")
    nota = CHECKINS / f"{fecha}.md"
    if nota.is_symlink() or not CHECKINS.is_dir():
        raise ValueError("carpeta de check-ins no válida")
    if base == "":
        with open(nota, "x", encoding="utf-8", newline="") as w:  # falla si alguien la creó entretanto
            w.write(texto)
        os.chown(nota, VAULT_UID, VAULT_UID)
        os.chmod(nota, 0o644)
        return
    if not nota.is_file() or hashlib.sha256(nota.read_bytes()).hexdigest() != base:
        raise ValueError("el check-in ha cambiado en el vault; recarga")
    with open(nota, "w", encoding="utf-8", newline="") as w:  # en el sitio: conserva dueño (1000) y permisos
        w.write(texto)


def foto(d, jpg: Path):
    fecha, pose = d["fecha"], d["pose"]
    if not FECHA.match(fecha) or pose not in POSES:
        raise ValueError("fecha o pose no válida")
    if not jpg.is_file() or jpg.is_symlink() or jpg.stat().st_size > 4_000_000:
        raise ValueError("falta la foto")
    with open(jpg, "rb") as r:
        if r.read(3) != b"\xff\xd8\xff":
            raise ValueError("no es una imagen JPEG")
    uid, gid = portal()
    for carpeta in (FOTOS, FOTOS / fecha):
        carpeta.mkdir(mode=0o750, exist_ok=True)
        if carpeta.is_symlink():
            raise ValueError("carpeta de fotos no válida")
        os.chown(carpeta, 0, gid)
        os.chmod(carpeta, 0o750)
    destino = FOTOS / fecha / f"{pose}.jpg"
    if destino.exists():
        destino.rename(destino.with_name(f"{pose}-antes-{datetime.now():%Y%m%d-%H%M%S}.jpg"))
    tmp = destino.with_suffix(".tmp")
    shutil.copyfile(jpg, tmp)
    os.chown(tmp, 0, gid)
    os.chmod(tmp, 0o640)
    tmp.replace(destino)


def carpeta(fecha: str) -> Path:
    """/srv/entreno-fotos/<fecha> (o .eliminadas), creada con root:portal 750."""
    gid = portal()[1]
    for c in (FOTOS, FOTOS / fecha):
        c.mkdir(mode=0o750, exist_ok=True)
        if c.is_symlink():
            raise ValueError("carpeta de fotos no válida")
        os.chown(c, 0, gid)
        os.chmod(c, 0o750)
    return FOTOS / fecha


def origen(d) -> Path:
    fecha, pose = d["fecha"], d["pose"]
    if not FECHA.match(fecha) or pose not in POSES:
        raise ValueError("fecha o pose no válida")
    f = FOTOS / fecha / f"{pose}.jpg"
    if not f.is_file() or f.is_symlink():
        raise ValueError("esa foto ya no está; recarga")
    return f


def mover(d):
    src = origen(d)
    a_fecha, a_pose = d["a_fecha"], d["a_pose"]
    if not FECHA.match(a_fecha) or a_pose not in POSES:
        raise ValueError("fecha o pose de destino no válida")
    dst = carpeta(a_fecha) / f"{a_pose}.jpg"
    if dst == src:
        return
    if dst.exists():  # ocupado: se intercambian
        tmp = src.with_name(f".cambio-{os.getpid()}.jpg")
        src.rename(tmp)
        dst.rename(src)
        tmp.rename(dst)
    else:
        src.rename(dst)


def eliminar(d):
    src = origen(d)
    destino = carpeta(".eliminadas") / f"{d['fecha']}-{d['pose']}-{datetime.now():%Y%m%d-%H%M%S}.jpg"
    src.rename(destino)


def procesar(f: Path):
    pid = f.stem
    jpg = f.with_suffix(".jpg")
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = str(d.get("id", pid))
        if d.get("op") == "checkin":
            checkin(d)
        elif d.get("op") == "foto":
            foto(d, jpg)
        elif d.get("op") == "mover":
            mover(d)
        elif d.get("op") == "eliminar":
            eliminar(d)
        else:
            raise ValueError("operación desconocida")
        resultado(pid, True)
    except FileExistsError:
        resultado(pid, False, "el check-in ha cambiado en el vault; recarga")
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"medidas-guardar: error con {f.name}: {e}")
    finally:
        f.unlink(missing_ok=True)
        jpg.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json")):
        procesar(f)
    ahora = time.time()
    for f in PENDIENTE.iterdir():  # restos que no son peticiones (una foto sin su .json pasado un minuto)
        if f.suffix != ".json" and ahora - f.lstat().st_mtime > 60:
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink(missing_ok=True)
