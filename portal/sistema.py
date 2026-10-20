#!/usr/bin/env python3
"""Panel Sistema: estado del VPS (Resumen, Memoria, Disco, Servicios, Usuarios, Procesos) y mapa de cómo está montado todo.

Solo lee: /proc, /etc/passwd, `who`, `systemctl show`, `apt list --upgradable` y el latido del vault
(estado-sistema.md, lo escribe /opt/salud/check.sh, que es quien ve los contenedores: el portal no tiene Docker).
Lo caro (tamaño de carpetas, actualizaciones) se guarda en memoria unos minutos.
"""
import json, os, re, shutil, subprocess, threading, time
from pathlib import Path

LATIDO = Path("/srv/vault/30 - Personal/estado-sistema.md")
SERVICIOS = [  # (unidad, qué es)
    ("portal.service", "Portal web"),
    ("finanzas-llave.service", "Caja de Finanzas"),
    ("docker.service", "Docker (Caddy, MCP, WebDAV, Obsidian)"),
    ("salud-sync.timer", "Fitbit y pasos, cada día"),
    ("salud-sync.path", "Pasos del iPhone"),
    ("todo-guardar.path", "Escritor To Do"),
    ("tareas-guardar.path", "Escritor Tareas"),
    ("listas-guardar.path", "Escritor Listas"),
    ("cv-guardar.path", "Escritor CV"),
    ("medidas-guardar.path", "Escritor Perímetros y Fotos"),
    ("entreno-sesion-guardar.path", "Escritor Entreno en directo"),
    ("estilo-guardar.path", "Escritor Estilo"),
    ("ssh.service", "SSH"),
    ("cron.service", "Cron (latido y backup)"),
]
CARPETAS = [("/srv/vault", "Vault"), ("/srv/entreno-fotos", "Fotos de Entreno"), ("/opt", "Código"),
            ("/var/lib/portal", "Estado del portal"), ("/var/lib/docker", "Docker")]
_cache = {}
_cerrojo = threading.Lock()


def _cacheado(clave, segundos, fn):
    with _cerrojo:
        v = _cache.get(clave)
        if v and time.time() - v[0] < segundos:
            return v[1]
    r = fn()
    with _cerrojo:
        _cache[clave] = (time.time(), r)
    return r


def _cmd(args, t=10):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=t).stdout
    except Exception:
        return ""


def memoria():
    m = {}
    for l in Path("/proc/meminfo").read_text().splitlines():
        k, v = l.split(":", 1)
        m[k] = int(v.split()[0]) * 1024
    return {"total": m["MemTotal"], "disponible": m["MemAvailable"], "libre": m["MemFree"],
            "cache": m.get("Cached", 0) + m.get("Buffers", 0) + m.get("SReclaimable", 0),
            "swap_total": m["SwapTotal"], "swap_usado": m["SwapTotal"] - m["SwapFree"]}


def discos():
    out, vistos = [], set()
    for l in Path("/proc/mounts").read_text().splitlines():
        dev, punto, tipo = l.split()[:3]
        if tipo not in ("ext4", "xfs", "btrfs", "vfat") or dev in vistos or punto.startswith("/var/lib/docker"):
            continue
        vistos.add(dev)
        try:
            u = shutil.disk_usage(punto)
        except OSError:
            continue
        out.append({"punto": punto, "dispositivo": dev, "tipo": tipo, "total": u.total, "usado": u.used, "libre": u.free})
    return out


def _tamano(ruta):
    total = 0
    for raiz, dirs, archivos in os.walk(ruta, onerror=lambda e: None):
        for a in archivos:
            try:
                total += os.lstat(os.path.join(raiz, a)).st_size
            except OSError:
                pass
    return total


def carpetas():
    def calc():
        out = []
        for r, n in CARPETAS:
            p = Path(r)
            try:
                ok = p.exists() and os.access(r, os.R_OK | os.X_OK)
            except OSError:
                ok = False
            out.append({"ruta": r, "nombre": n, "bytes": _tamano(r) if ok else None})
        return out
    return _cacheado("carpetas", 1800, calc)


def servicios():
    unidades = [u for u, _ in SERVICIOS]
    txt = _cmd(["systemctl", "show", "--no-pager", "-p", "Id,ActiveState,SubState,MemoryCurrent,ActiveEnterTimestamp,NRestarts", *unidades])
    bloques = {}
    for b in txt.strip().split("\n\n"):
        d = dict(l.split("=", 1) for l in b.splitlines() if "=" in l)
        if d.get("Id"):
            bloques[d["Id"]] = d
    out = []
    for u, n in SERVICIOS:
        d = bloques.get(u, {})
        mem = d.get("MemoryCurrent", "")
        out.append({"unidad": u, "nombre": n, "estado": d.get("ActiveState", "-"), "sub": d.get("SubState", ""),
                    "memoria": int(mem) if mem.isdigit() and int(mem) < 1 << 60 else None,
                    "desde": d.get("ActiveEnterTimestamp", ""), "reinicios": d.get("NRestarts", "")})
    # Claude Code (Remote Control) es un servicio de usuario de `claude`: se ve por su proceso.
    rc = [p for p in procesos(todos=True) if "remote-control" in p["cmd"]]
    out.append({"unidad": "claude-remote (usuario claude)", "nombre": "Claude Code para el móvil",
                "estado": "active" if rc else "inactive", "sub": "running" if rc else "dead",
                "memoria": sum(p["rss"] for p in rc) or None, "desde": "", "reinicios": ""})
    return out


def procesos(todos=False):
    usuarios = {int(l.split(":")[2]): l.split(":")[0] for l in Path("/etc/passwd").read_text().splitlines() if l.count(":") >= 6}
    out = []
    for p in Path("/proc").iterdir():
        if not p.name.isdigit():
            continue
        try:
            st = (p / "status").read_text()
            cmd = (p / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace").strip()
        except OSError:
            continue
        rss = re.search(r"VmRSS:\s+(\d+)", st)
        uid = int(re.search(r"Uid:\s+(\d+)", st).group(1))
        nombre = re.search(r"Name:\s+(.*)", st).group(1)
        if re.fullmatch(r"[\d.]+", nombre) and "claude" in cmd:  # el binario de Claude Code se llama por su versión
            nombre = "claude"
        out.append({"pid": int(p.name), "nombre": nombre, "usuario": usuarios.get(uid, str(uid)),
                    "rss": int(rss.group(1)) * 1024 if rss else 0, "cmd": cmd[:160]})
    out.sort(key=lambda x: -x["rss"])
    return out if todos else out[:15]


def usuarios():
    cuentas = []
    for l in Path("/etc/passwd").read_text().splitlines():
        c = l.split(":")
        if len(c) < 7:
            continue
        uid = int(c[2])
        if uid == 0 or (1000 <= uid < 60000):
            cuentas.append({"nombre": c[0], "uid": uid, "carpeta": c[5], "shell": c[6],
                            "entra": not c[6].endswith(("nologin", "false"))})
    sesiones = []
    for l in _cmd(["who"]).splitlines():
        p = l.split()
        if len(p) >= 4:
            m = re.search(r"\(([^)]*)\)", l)
            sesiones.append({"usuario": p[0], "terminal": p[1], "desde": " ".join(p[2:4]), "origen": m.group(1) if m else "local"})
    return {"cuentas": cuentas, "sesiones": sesiones}


def actualizaciones():
    def calc():
        lineas = [l for l in _cmd(["apt", "list", "--upgradable"], 30).splitlines() if "/" in l]
        return {"paquetes": [l.split("/")[0] for l in lineas], "reinicio": Path("/run/reboot-required").exists()}
    return _cacheado("apt", 3600, calc)


def latido():
    try:
        t = LATIDO.read_text(encoding="utf-8")
    except OSError:
        return {"actualizado": "", "estado": "-", "edad_min": None}
    d = dict(re.findall(r"^(\w+):\s*(.*)$", t, re.M))
    edad = None
    try:
        edad = int((time.time() - time.mktime(time.strptime(d.get("actualizado", ""), "%Y-%m-%d %H:%M"))) // 60)
    except ValueError:
        pass
    return {"actualizado": d.get("actualizado", ""), "estado": d.get("estado", "-"), "edad_min": edad}


def datos():
    up = float(Path("/proc/uptime").read_text().split()[0])
    carga = Path("/proc/loadavg").read_text().split()[:3]
    u = os.uname()
    return {"generado": time.strftime("%H:%M:%S"), "host": u.nodename, "kernel": u.release,
            "cpus": os.cpu_count(), "uptime": int(up), "carga": [float(x) for x in carga],
            "memoria": memoria(), "discos": discos(), "carpetas": carpetas(), "servicios": servicios(),
            "procesos": procesos(), "usuarios": usuarios(), "actualizaciones": actualizaciones(), "latido": latido()}


def html(plantilla: Path) -> str:
    js = json.dumps(datos(), ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.read_text(encoding="utf-8").replace("/*__DATOS__*/null", js)


if __name__ == "__main__":
    print(json.dumps(datos(), ensure_ascii=False, indent=1)[:3000])
