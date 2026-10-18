#!/usr/bin/env python3
"""Baja de Google Health (reloj Fitbit) el sueño y las constantes y las apunta en el diario de Entreno.

- Mira los últimos DIAS días y, en cada nota diaria que ya exista, rellena SOLO los campos vacíos
  del frontmatter: sueno (horas dormidas, p. ej. 7h18), hrv (ms), fc_reposo (lpm), fr_sueno (resp/min).
  Nunca sobrescribe un valor que ya esté, nunca crea notas (un día sin nota se rellena cuando aparezca).
- El sueño cuenta para el día en que te despiertas (sueño principal).
- Pasos: el mayor entre Fitbit y el iPhone (Atajo de iOS → portal). Solo sube, nunca baja; al subirlos
  ajusta la fila "Pasos" de "Deporte y gasto" y recalcula kcal_actividad, kcal_gastadas y balance.
- Deja el estado en /var/lib/salud-sync/estado.json (lo enseña el portal) y una línea en /var/log/salud-sync.log.
Uso:  python3 sincronizar.py [--dias 7] [--prueba]   (--prueba: enseña lo que haría sin escribir)
"""
import json, os, re, sys, urllib.error, urllib.parse, urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CRED = Path("/etc/salud-sync/google.json")
DIARIO = Path("/srv/vault/40 - Entreno/20-diario")
ESTADO = Path("/var/lib/salud-sync/estado.json")
LOG = Path("/var/log/salud-sync.log")
PASOS_IPHONE = Path("/var/lib/portal/pasos-iphone.json")  # lo escribe el portal (/api/pasos, Atajo de iOS)
API = "https://health.googleapis.com/v4/users/me/dataTypes/"
DIAS = int(sys.argv[sys.argv.index("--dias") + 1]) if "--dias" in sys.argv else 7
PRUEBA = "--prueba" in sys.argv
UID_VAULT = 1000


def token():
    c = json.loads(CRED.read_text())
    datos = urllib.parse.urlencode({"client_id": c["client_id"], "client_secret": c["client_secret"],
                                    "refresh_token": c["refresh_token"], "grant_type": "refresh_token"}).encode()
    return json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", datos, timeout=30))["access_token"]


def pedir(tok, ruta, params):
    puntos, pagina = [], None
    while True:
        p = dict(params, **({"pageToken": pagina} if pagina else {}))
        r = urllib.request.Request(API + ruta + "?" + urllib.parse.urlencode(p), headers={"Authorization": "Bearer " + tok})
        d = json.load(urllib.request.urlopen(r, timeout=30))
        puntos += d.get("dataPoints", [])
        pagina = d.get("nextPageToken")
        if not pagina:
            return puntos


def fecha(d):
    return f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"


def leer(tok, desde: date) -> dict:
    """{fecha: {campo: valor en texto}}"""
    out = {}
    def poner(f, k, v):
        out.setdefault(f, {})[k] = v
    # sueño principal, por fecha de despertar (hora local)
    for p in pedir(tok, "sleep/dataPoints", {"filter": f'sleep.interval.civil_end_time >= "{desde}"', "page_size": 25}):
        s = p["sleep"]
        if not s.get("metadata", {}).get("mainSleep"):
            continue
        fin = datetime.fromisoformat(s["interval"]["endTime"].replace("Z", "+00:00")) + \
            timedelta(seconds=int(s["interval"].get("endUtcOffset", "0s").rstrip("s")))
        minutos = int(s.get("summary", {}).get("minutesAsleep", 0))
        if minutos:
            poner(fin.date().isoformat(), "sueno", f"{minutos // 60}h{minutos % 60:02d}")
    diarios = [("daily-heart-rate-variability", "dailyHeartRateVariability", "averageHeartRateVariabilityMilliseconds", "hrv", 0),
               ("daily-resting-heart-rate", "dailyRestingHeartRate", "beatsPerMinute", "fc_reposo", 0),
               ("daily-respiratory-rate", "dailyRespiratoryRate", "breathsPerMinute", "fr_sueno", 1)]
    for tipo, clave, campo, nombre, dec in diarios:
        filtro = f'{tipo.replace("-", "_")}.date >= "{desde}"'
        for p in pedir(tok, f"{tipo}/dataPoints", {"filter": filtro}):
            x = p.get(clave, {})
            if campo in x:
                v = round(float(x[campo]), dec)
                poner(fecha(x["date"]), nombre, f"{v:.{dec}f}" if dec else str(int(v)))
    return out


def pasos_fitbit(tok, desde: date) -> dict:
    hasta = date.today() + timedelta(days=1)
    cuerpo = {"range": {"start": {"date": {"year": desde.year, "month": desde.month, "day": desde.day}},
                        "end": {"date": {"year": hasta.year, "month": hasta.month, "day": hasta.day}}},
              "windowSizeDays": 1}
    r = urllib.request.Request(API + "steps/dataPoints:dailyRollUp", data=json.dumps(cuerpo).encode(),
                               headers={"Authorization": "Bearer " + tok, "Content-Type": "application/json"})
    d = json.load(urllib.request.urlopen(r, timeout=30))
    return {fecha(p["civilStartTime"]["date"]): int(p["steps"]["countSum"]) for p in d.get("rollupDataPoints", [])}


def pasos_iphone() -> dict:
    try:
        return {f: v["pasos"] for f, v in json.loads(PASOS_IPHONE.read_text()).items()}
    except (OSError, ValueError, KeyError):
        return {}


def numero(t):
    """'11.000' → 11000, '~1.373' → 1373, '86.6' → 86.6, '' → None."""
    m = re.search(r"-?\d[\d.,]*", (t or "").replace("−", "-"))
    if not m:
        return None
    n = m.group().rstrip(".,")
    if re.fullmatch(r"-?\d{1,3}([.,]\d{3})+", n):
        return float(re.sub(r"[.,]", "", n))
    try:
        return float(n.replace(",", "."))
    except ValueError:
        return None


def peso_del_dia(f: str, fm: str):
    m = re.search(r"^peso:[ \t]*(.*)$", fm, re.M)
    p = numero(m.group(1)) if m else None
    if p:
        return p
    for ruta in sorted(DIARIO.glob("*/*/*.md"), reverse=True):  # último peso conocido antes de ese día
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", ruta.stem) and ruta.stem < f:
            m = re.search(r"^peso:[ \t]*(\S.*)$", ruta.read_text(encoding="utf-8"), re.M)
            if m and numero(m.group(1)):
                return numero(m.group(1))
    return None


def poner_campo(fm: str, k: str, v) -> str:
    m = re.search(rf"^{k}:[ \t]*(.*)$", fm, re.M)
    return fm[:m.start()] + f"{k}: {v}" + fm[m.end():] if m else fm + f"\n{k}: {v}"


def subir_pasos(ruta: Path, f: str, nuevos: int) -> str:
    """Si `nuevos` supera los pasos apuntados: los sube, ajusta la fila Pasos de "Deporte y gasto"
    (pasos × peso × 0.00045, datos-y-constantes.md) y recalcula actividad, gastadas y balance."""
    texto = ruta.read_text(encoding="utf-8")
    if not texto.startswith("---\n") or "\n---" not in texto[4:]:
        return ""
    fin = texto.index("\n---", 4)
    fm, cuerpo = texto[:fin], texto[fin:]
    m = re.search(r"^pasos:[ \t]*(.*)$", fm, re.M)
    actual = numero(m.group(1)) if m else None
    if actual is not None and actual >= nuevos:
        return ""
    peso = peso_del_dia(f, fm)
    fm = poner_campo(fm, "pasos", nuevos)
    nota = f"pasos {int(actual) if actual is not None else '—'}→{nuevos}"
    if not peso:
        escribir(ruta, fm + cuerpo)
        return nota + " (sin peso conocido: fila de gasto sin tocar)"
    kcal = round(nuevos * peso * 0.00045)
    detalle = f"{nuevos:,}".replace(",", ".")
    # tabla "Deporte y gasto"
    lineas = cuerpo.split("\n")
    try:
        i = next(n for n, l in enumerate(lineas) if l.strip().lower().startswith("## deporte"))
    except StopIteration:
        escribir(ruta, fm + cuerpo)
        return nota + " (sin tabla de gasto)"
    tabla = [n for n in range(i + 1, len(lineas)) if lineas[n].strip().startswith("|")]
    tabla = [n for k, n in enumerate(tabla) if n == tabla[0] + k]  # solo el bloque seguido
    if len(tabla) < 2:
        escribir(ruta, fm + cuerpo)
        return nota + " (tabla de gasto rara)"
    celdas = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    cab = [c.lower() for c in celdas(lineas[tabla[0]])]
    ck = next((k for k, c in enumerate(cab) if c.startswith("kcal")), len(cab) - 1)
    fila_pasos = next((n for n in tabla[2:] if celdas(lineas[n])[0].lower().startswith("pasos")), None)
    if fila_pasos is not None:
        c = celdas(lineas[fila_pasos])
        c[1], c[ck] = detalle, str(kcal)
        lineas[fila_pasos] = "| " + " | ".join(c) + " |"
    else:
        c = ["Pasos", detalle] + ["—"] * (len(cab) - 2)
        c[ck] = str(kcal)
        lineas.insert(tabla[-1] + 1, "| " + " | ".join(c) + " |")
        tabla.append(tabla[-1] + 1)
    kcals = [numero(celdas(lineas[n])[ck]) for n in tabla[2:]]
    cuerpo = "\n".join(lineas)
    nota += f" ({kcal} kcal)"
    if None not in kcals:
        act = round(sum(kcals))
        fm = poner_campo(fm, "kcal_actividad", act)
        mb = re.search(r"^kcal_basal:[ \t]*(.*)$", fm, re.M)
        basal = numero(mb.group(1)) if mb else None
        if basal:
            gast = round(basal + act)
            fm = poner_campo(fm, "kcal_gastadas", gast)
            mc = re.search(r"^kcal_consumidas:[ \t]*(.*)$", fm, re.M)
            cons = numero(mc.group(1)) if mc else None
            mbal = re.search(r"^balance:[ \t]*(.*)$", fm, re.M)
            if cons is not None and mbal and mbal.group(1).strip():  # balance vacío = día no válido: se respeta
                fm = poner_campo(fm, "balance", round(cons - gast))
    else:
        nota += " (actividad sin recalcular: hay una fila sin kcal)"
    escribir(ruta, fm + cuerpo)
    return nota


def escribir(ruta: Path, texto: str):
    if PRUEBA:
        return
    tmp = ruta.with_name("." + ruta.name + ".tmp")
    tmp.write_text(texto, encoding="utf-8")
    os.chown(tmp, UID_VAULT, UID_VAULT)
    os.chmod(tmp, 0o644)
    tmp.replace(ruta)


def rellenar(ruta: Path, valores: dict) -> list:
    texto = ruta.read_text(encoding="utf-8")
    if not texto.startswith("---\n") or "\n---" not in texto[4:]:
        return []
    fin = texto.index("\n---", 4)
    fm, resto = texto[:fin], texto[fin:]
    hechos = []
    for k, v in valores.items():
        m = re.search(rf"^{k}:[ \t]*(.*)$", fm, re.M)
        if m and m.group(1).strip():
            continue  # ya tiene valor: no se toca
        if m:
            fm = fm[:m.start()] + f"{k}: {v}" + fm[m.end():]
        else:
            fm += f"\n{k}: {v}"
        hechos.append(f"{k}={v}")
    if hechos and not PRUEBA:
        tmp = ruta.with_name("." + ruta.name + ".tmp")
        tmp.write_text(fm + resto, encoding="utf-8")
        os.chown(tmp, UID_VAULT, UID_VAULT)
        os.chmod(tmp, 0o644)
        tmp.replace(ruta)
    return hechos


def guardar_estado(**k):
    if PRUEBA:
        return
    ESTADO.parent.mkdir(mode=0o755, exist_ok=True)
    previo = json.loads(ESTADO.read_text()) if ESTADO.exists() else {}
    previo.update(k, ultima_ejecucion=datetime.now().strftime("%Y-%m-%d %H:%M"))
    ESTADO.write_text(json.dumps(previo, ensure_ascii=False))
    os.chmod(ESTADO, 0o644)


def log(linea):
    print(linea)
    if not PRUEBA:
        with LOG.open("a") as f:
            f.write(f"{datetime.now():%Y-%m-%d %H:%M} {linea}\n")


def main():
    desde = date.today() - timedelta(days=DIAS)
    try:
        tok = token()
    except urllib.error.HTTPError as e:
        err = e.read().decode()[:200]
        guardar_estado(ok=False, error="Google no renueva el permiso: hay que volver a autorizar (autorizar.py)")
        return log(f"ERROR permiso: {err}")
    try:
        datos = leer(tok, desde)
    except Exception as e:
        guardar_estado(ok=False, error=f"Error leyendo Google Health: {e}"[:200])
        return log(f"ERROR lectura: {e}")
    try:
        fit = pasos_fitbit(tok, desde)
    except Exception as e:
        fit = {}
        log(f"AVISO pasos Fitbit: {e}")
    ip_total = pasos_iphone()
    ip = {f: v for f, v in ip_total.items() if f >= desde.isoformat()}
    for f in set(fit) | set(ip):
        datos.setdefault(f, {})
    cambios, sin_nota = [], []
    for f, vals in sorted(datos.items()):
        ruta = DIARIO / f[:4] / f[5:7] / f"{f}.md"
        if not ruta.exists():
            sin_nota.append(f)
            continue
        h = rellenar(ruta, vals)
        # Hasta que el Atajo haya mandado algo nunca, no se tocan pasos. Hoy y ayer esperan al iPhone
        # (el Atajo manda a las 23:15); si un día más viejo no llegó del iPhone, vale solo Fitbit.
        reciente = f >= (date.today() - timedelta(days=1)).isoformat()
        listo = bool(ip_total) and (f in ip or not reciente)
        maximo = max(fit.get(f, 0), ip.get(f, 0)) if listo else 0
        if maximo:
            p = subir_pasos(ruta, f, maximo)
            if p:
                h.append(p + f" [fitbit {fit.get(f, '—')}, iphone {ip.get(f, '—')}]")
        if h:
            cambios.append(f"{f}: {', '.join(h)}")
    guardar_estado(ok=True, error="", ultimo_ok=datetime.now().strftime("%Y-%m-%d %H:%M"))
    log(("OK " if cambios else "OK sin cambios") + "; ".join(cambios) + (f" | sin nota: {', '.join(sin_nota)}" if sin_nota else ""))


if __name__ == "__main__":
    main()
