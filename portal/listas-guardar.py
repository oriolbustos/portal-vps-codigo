#!/usr/bin/env python3
"""Aplica en el vault los cambios hechos en el panel Listas del portal (/listas/).

El portal deja cada operación en /var/lib/portal/listas-pendiente/<id>.json y listas-guardar.path lanza
esto (root). Solo toca notas .md de "30 - Personal/10 - Listas/" (no subcarpetas) y trabaja línea a
línea sobre la nota actual, así que no pisa lo que se haya editado a la vez en Obsidian:
- anadir: una línea nueva al final de la sección elegida ("- [ ] x" si la lista usa casillas).
- marcar: "- [ ] x" <-> "- [x] x ✅ AAAA-MM-DD", como el plugin Tasks de Obsidian.
- archivar: mueve la línea (y sus "- " de detalle) a "## Archivado" al final. Nunca se borra nada.
Si la línea ya no está como se vio en el panel, no hace nada y avisa.
"""
import json, os, pwd, re, time
from pathlib import Path

CARPETA = Path("/srv/vault/60 - Lists")
PENDIENTE = Path("/var/lib/portal/listas-pendiente")
RESULTADO = Path("/var/lib/portal/listas-resultado.json")
CASILLA = re.compile(r"^(\s*)- \[([ xX])\] (.*)$")
TITULO = re.compile(r"^#{1,6}\s+(.*)$")
ARCHIVO = "Archivado"


def resultado(pid, ok, error=""):
    tmp = RESULTADO.with_suffix(".tmp")
    tmp.write_text(json.dumps({"id": pid, "ok": ok, "error": error}, ensure_ascii=False))
    u = pwd.getpwnam("portal")
    os.chown(tmp, u.pw_uid, u.pw_gid)
    os.chmod(tmp, 0o600)
    tmp.replace(RESULTADO)


def buscar(lineas, n, linea):
    if 0 <= n < len(lineas) and lineas[n] == linea:
        return n
    if lineas.count(linea) == 1:
        return lineas.index(linea)
    raise ValueError("esa línea ha cambiado en el vault. Recarga la página")


def usa_casillas(lineas):
    llenas = [l for l in lineas if l.strip()]
    return sum(bool(CASILLA.match(l)) for l in lineas) >= max(1, len(llenas) // 2)


def anadir(lineas, seccion, texto):
    nueva = f"- [ ] {texto}" if usa_casillas(lineas) else texto
    titulos = [i for i, l in enumerate(lineas) if TITULO.match(l)]
    if seccion:
        ini = next((i for i in titulos if TITULO.match(lineas[i]).group(1).strip() == seccion), None)
        if ini is None:
            raise ValueError("no encuentro esa sección")
        fin = next((i for i in titulos if i > ini), len(lineas))
    else:  # sin sección: al final de lo que hay antes del primer título
        ini, fin = -1, titulos[0] if titulos else len(lineas)
    pos = fin
    while pos - 1 > ini and (not lineas[pos - 1].strip() or re.fullmatch(r"\s*-{3,}\s*", lineas[pos - 1])):
        pos -= 1  # después del último elemento, antes de huecos y separadores
    lineas.insert(pos, nueva)


def marcar(lineas, n, linea, hecho):
    i = buscar(lineas, n, linea)
    c = CASILLA.match(lineas[i])
    if not c:
        raise ValueError("esa línea no es una casilla")
    texto = re.sub(r"\s*✅\s*\d{4}-\d{2}-\d{2}\s*$", "", c.group(3).rstrip())
    lineas[i] = f"{c.group(1)}- [x] {texto} ✅ {time.strftime('%Y-%m-%d')}" if hecho else f"{c.group(1)}- [ ] {texto}"


def archivar(lineas, n, linea):
    i = buscar(lineas, n, linea)
    j = i + 1
    if not CASILLA.match(lineas[i]):  # se lleva su detalle ("- ...") en listas de líneas sueltas
        while j < len(lineas) and re.match(r"^\s*-\s+\S", lineas[j]) and not CASILLA.match(lineas[j]):
            j += 1
    bloque = lineas[i:j]
    del lineas[i:j]
    if not any(TITULO.match(l) and TITULO.match(l).group(1).strip() == ARCHIVO for l in lineas):
        while lineas and not lineas[-1].strip():
            lineas.pop()
        lineas += ["", f"## {ARCHIVO}"]
    lineas += bloque


def procesar(f: Path):
    pid = f.stem
    try:
        d = json.loads(f.read_text(encoding="utf-8"))
        pid = str(d.get("id", pid))
        archivo = d["archivo"]
        destino = CARPETA / archivo
        if not re.fullmatch(r"[^/\\]+\.md", archivo) or destino.resolve().parent != CARPETA.resolve() or not destino.is_file():
            raise ValueError("esa lista no existe")
        bruto = destino.read_text(encoding="utf-8")
        final = bruto.endswith("\n")
        lineas = bruto.splitlines()
        if d["op"] == "anadir":
            texto = re.sub(r"\s+", " ", d["texto"]).strip()
            if not texto or len(texto) > 300:
                raise ValueError("texto vacío o demasiado largo")
            anadir(lineas, d.get("seccion", ""), texto)
        elif d["op"] == "marcar":
            marcar(lineas, int(d["n"]), d["linea"], bool(d["hecho"]))
        elif d["op"] == "archivar":
            archivar(lineas, int(d["n"]), d["linea"])
        else:
            raise ValueError("operación desconocida")
        with open(destino, "w", encoding="utf-8") as w:  # en el sitio: conserva dueño (1000) y permisos
            w.write("\n".join(lineas) + ("\n" if final else ""))
        resultado(pid, True)
        print(f"listas-guardar: {d['op']} en {archivo}")
    except Exception as e:
        resultado(pid, False, str(e))
        print(f"listas-guardar: error con {f.name}: {e}")
    finally:
        f.unlink(missing_ok=True)


if __name__ == "__main__":
    for f in sorted(PENDIENTE.glob("*.json"), key=lambda x: x.stat().st_mtime):
        procesar(f)
