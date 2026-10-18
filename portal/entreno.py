#!/usr/bin/env python3
"""Panel de entreno: lee las notas diarias del vault y genera un HTML autocontenido.

Fuente: /srv/vault/40 - Entreno/20-diario/AAAA/MM/AAAA-MM-DD.md (frontmatter + tablas
"Comidas", "Entreno" y "Deporte y gasto"). Solo lee; nunca escribe en el vault.

El parseo es tolerante: reps "?", "10 + 6", "30 s"; pesos "4.5 → 2.3", "6+6",
"12.5/mano", "36 ayuda", "corporal"; huecos en el frontmatter y días sin nota.

Uso manual:  python3 entreno.py [--salida panel.html] [--json]
"""
import json, re, sys, unicodedata
from datetime import date, datetime
from pathlib import Path

DIARIO = Path("/srv/vault/40 - Entreno/20-diario")
PLAN_NUTRICION = DIARIO.parent / "10-referencia" / "plan-nutricion.md"
HISTORICO = DIARIO.parent / "40 - historico" / "entreno"   # push.md, pull.md, pierna.md: "Estado actual"
TIPOS = ("push", "pierna", "pull")                          # rotación (instructions.md §2.1)
NOMBRE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})\.md$")
NUM = re.compile(r"[-−–]?\d+(?:[.,]\d+)?")

# Ejercicio → (nombre común, sesión, asistido). Se prueban en orden sobre el nombre
# en minúsculas y sin tildes. Lo que no encaja se queda con su nombre en "otros".
REGLAS = [
    (r"dips|fondos", "Dips (asistida)", "push", True),
    (r"press pecho maquina", "Press pecho máquina", "push", False),
    (r"press pecho en suelo", "Press pecho en suelo", "push", False),
    (r"press (pecho|mancuernas banco)", "Press pecho mancuernas", "push", False),
    (r"flexiones", "Flexiones", "push", False),
    (r"apertura|chest fl", "Aperturas", "push", False),
    (r"elevaciones laterales", "Elevaciones laterales", "push", False),
    (r"press hombro de pie", "Press hombro de pie", "push", False),
    (r"maquina hombro", "Press hombro máquina", "push", False),
    (r"press hombro", "Press hombro mancuernas", "push", False),
    (r"triceps.*(extension|nuca)|extension triceps", "Tríceps extensión", "push", False),
    (r"triceps", "Tríceps polea", "push", False),
    (r"dominada.*libre", "Dominada libre", "pull", False),
    (r"dominada", "Dominadas asistidas", "pull", True),
    (r"jalon", "Jalón", "pull", False),
    (r"remo unilateral", "Remo unilateral", "pull", False),
    (r"remo sentado|remo bajo", "Remo sentado", "pull", False),
    (r"remo (con|horizontal) barra|yates", "Remo con barra", "pull", False),
    (r"trx|remo/pull", "Pull TRX", "pull", False),
    (r"face pull", "Face pull", "pull", False),
    (r"deltoides posterior", "Deltoides posterior", "pull", False),
    (r"curl martillo", "Curl martillo", "pull", False),
    (r"curl biceps", "Curl bíceps", "pull", False),
    (r"isometric.*cuadriceps|cuadriceps.*isometric", "Extensión cuádriceps (isométrico)", "pierna", False),
    (r"extension (de )?(cuadriceps|piernas)", "Extensión cuádriceps 90-45°", "pierna", False),
    (r"prensa", "Prensa horizontal", "pierna", False),
    (r"(peso muerto|pm) rumano", "Peso muerto rumano", "pierna", False),
    (r"peso muerto", "Peso muerto (prueba)", "pierna", False),
    (r"curl femoral", "Curl femoral", "pierna", False),
    (r"abductor", "Abductor", "pierna", False),
    (r"aductor", "Aductor", "pierna", False),
    (r"abdominal|core", "Core", "pierna", False),
]


def plano(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def num(s):
    """Primer número de un texto ('~1.939', '−2237', '86,6 kg') o None."""
    if s is None:
        return None
    m = NUM.search(str(s).replace("−", "-"))
    if not m:
        return None
    t = m.group().replace("−", "-").replace("–", "-")
    if re.fullmatch(r"-?\d{1,3}\.\d{3}", t):  # 9.000 pasos → 9000
        t = t.replace(".", "")
    try:
        return float(t.replace(",", "."))
    except ValueError:
        return None


def clasificar(nombre: str):
    p = plano(nombre)
    for patron, comun, sesion, asistido in REGLAS:
        if re.search(patron, p):
            return comun, sesion, asistido
    limpio = re.sub(r"\s*\(.*?\)", "", nombre).strip() or nombre
    return limpio, "otros", False


def leer_reps(texto: str):
    """→ (reps de la serie principal, reps totales, segundos?) o (None, None, False)."""
    t = (texto or "").strip()
    seg = bool(re.search(r"\d\s*(s|seg|\")\b", t))
    m = re.search(r"(\d+)\s*[x×]\s*(\d+)", t)
    if m:  # "3x12": el grande son reps
        r = max(int(m.group(1)), int(m.group(2)))
        return r, r, seg
    nums = [int(float(n.replace(",", "."))) for n in re.findall(r"\d+(?:[.,]\d+)?", t)]
    if not nums:
        return None, None, seg
    return nums[0], sum(nums), seg


def leer_peso(texto: str):
    """→ (kg o None, nota). Toma la carga principal: '4.5 → 2.3' → 4.5 (drop);
    '6+6' → 6 (por mano); '25 (12.5/mano)' → 12.5; 'corporal' → None."""
    t = (texto or "").replace(",", ".").strip()
    p = plano(t)
    nota = []
    if "→" in t or "->" in t:
        nota.append("drop")
    m = re.search(r"(\d+(?:\.\d+)?)\s*(/|por|x)\s*mano", p)
    if m:
        return float(m.group(1)), " ".join(nota + ["por mano"])
    nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", t)]
    if not nums:
        return None, "corporal" if "corporal" in p else ""
    if len(nums) == 2 and "+" in t and nums[0] == nums[1]:
        nota.append("por mano")
    elif len(nums) >= 2 and "+" in t:
        nota.append("drop")
    return nums[0], " ".join(nota)


def horas(texto):
    """'7h26', '7:26', '7.4' → horas en decimal."""
    t = (texto or "").strip()
    m = re.match(r"(\d+)\s*[h:]\s*(\d+)?", t)
    if m:
        return round(int(m.group(1)) + int(m.group(2) or 0) / 60, 2)
    return num(t)


def rir(texto: str):
    m = re.search(r"RIR\s*(\d+(?:[.,]\d)?)", texto or "", re.I)
    return float(m.group(1).replace(",", ".")) if m else None


def frontmatter(texto: str) -> dict:
    if not texto.startswith("---"):
        return {}
    fin = texto.find("\n---", 3)
    if fin < 0:
        return {}
    datos = {}
    for linea in texto[3:fin].splitlines():
        if ":" in linea and not linea.startswith((" ", "\t", "#")):
            k, v = linea.split(":", 1)
            datos[k.strip()] = v.strip().strip('"').strip("'")
    return datos


def secciones(texto: str) -> dict:
    """{'entreno': [filas como dict], ...} para cada '## Título' con tabla."""
    out, actual = {}, None
    cab = None
    for linea in texto.splitlines():
        if linea.startswith("## "):
            actual = plano(linea[3:].strip())
            out[actual] = []
            cab = None
            continue
        if actual is None or not linea.strip().startswith("|"):
            continue
        celdas = [c.strip() for c in linea.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-*:?", c) for c in celdas):
            continue
        if cab is None:
            cab = [plano(c) for c in celdas]
            continue
        if not any(celdas):
            continue
        out[actual].append({cab[i] if i < len(cab) else f"c{i}": c for i, c in enumerate(celdas)})
    return out


def col(fila: dict, *claves):
    for k, v in fila.items():
        if any(k.startswith(c) for c in claves):
            return v
    return ""


def leer_dia(ruta: Path, fecha: str) -> dict:
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    fm = frontmatter(texto)
    sec = secciones(texto)
    avisos = []

    def f(k):
        return num(fm.get(k))

    dia = {"fecha": fecha}
    for k in ("peso", "pasos", "kcal_consumidas", "prot_total", "carb_total", "grasa_total",
              "kcal_actividad", "kcal_basal", "kcal_gastadas", "balance", "azucar_libre_total"):
        dia[k] = f(k)
    if dia["peso"] is not None and not 40 <= dia["peso"] <= 200:
        avisos.append(f"peso raro: {fm.get('peso')}")
        dia["peso"] = None
    comidas = sec.get("comidas", [])
    if not comidas and not dia["kcal_consumidas"]:
        dia["kcal_consumidas"] = None  # sin comidas apuntadas: no es un día a 0 kcal
    for k in ("kcal_basal", "kcal_gastadas"):  # 0 = plantilla sin rellenar
        if not dia[k]:
            dia[k] = None
    if dia["kcal_gastadas"] is None or dia["kcal_consumidas"] is None:
        dia["balance"] = None
    dia["sueno"] = horas(fm.get("sueno"))
    dia["hrv"], dia["fc_reposo"] = f("hrv"), f("fc_reposo")
    dia["confirmado"] = fm.get("balance_confirmado", "").lower() == "true"
    dia["cheat"] = fm.get("cheat", "").lower() == "true"
    dia["hipo"] = sum(1 for c in comidas if "[hipo]" in col(c, "que").lower())
    dia["comidas"] = [{"hora": col(c, "hora"), "que": col(c, "que"), "kcal": num(col(c, "kcal")),
                       "p": num(col(c, "p (g)")), "c": num(col(c, "c (g)")), "g": num(col(c, "g (g)"))}
                      for c in comidas if col(c, "que")]

    series, ultimo = [], ""
    for fila in sec.get("entreno", []):
        nombre = col(fila, "ejercicio")
        if not nombre:
            if not ultimo:
                continue
            nombre = ultimo
            avisos.append("serie sin nombre de ejercicio")
        ultimo = nombre
        reps_txt, peso_txt, notas = col(fila, "reps"), col(fila, "peso"), col(fila, "notas")
        reps, reps_tot, seg = leer_reps(reps_txt)
        kg, nota_peso = leer_peso(peso_txt)
        comun, sesion, asistido = clasificar(nombre)
        if not asistido and re.search(r"ayuda|asist", plano(peso_txt + " " + nombre)):
            asistido = True
        series.append({
            "ej": comun, "orig": nombre, "sesion": sesion, "asistido": asistido,
            "serie": col(fila, "serie"), "reps": reps, "reps_tot": reps_tot, "seg": seg,
            "kg": kg, "nota_kg": nota_peso, "rir": rir(notas),
            "reps_txt": reps_txt, "kg_txt": peso_txt, "notas": notas,
        })
        if reps is None:
            avisos.append(f"{nombre}: reps '{reps_txt or 'vacío'}'")
    dia["series"] = series

    deporte = []
    for fila in sec.get("deporte y gasto", []):
        act = col(fila, "actividad")
        if act:
            deporte.append({"act": act, "kcal": num(col(fila, "kcal")), "dur": col(fila, "duracion")})
    dia["deporte"] = deporte

    # Tipo de sesión: la que tiene más series; "doble" si dos pasan de 4 series.
    cuenta = {}
    for s in series:
        if s["sesion"] != "otros":
            cuenta[s["sesion"]] = cuenta.get(s["sesion"], 0) + 1
    tipos = sorted(cuenta, key=cuenta.get, reverse=True)
    dia["sesiones"] = [t for t in tipos if cuenta[t] >= 4] or tipos[:1]
    dia["avisos"] = avisos
    return dia


def leer_todo(raiz: Path = DIARIO) -> list:
    dias = []
    for ruta in sorted(raiz.glob("*/*/*.md")):
        m = NOMBRE.match(ruta.name)
        if not m:
            continue
        try:
            fecha = date(int(m[1]), int(m[2]), int(m[3])).isoformat()
            dias.append(leer_dia(ruta, fecha))
        except Exception as e:  # una nota rota no tumba el panel
            dias.append({"fecha": ruta.stem, "series": [], "deporte": [], "sesiones": [],
                         "avisos": [f"no se pudo leer: {e}"]})
    return dias


def resumen_ejercicios(dias: list) -> dict:
    """Una entrada por ejercicio y día con la serie top y el resto de series."""
    ejercicios = {}
    for d in dias:
        por_ej = {}
        for s in d.get("series", []):
            por_ej.setdefault(s["ej"], []).append(s)
        for ej, ss in por_ej.items():
            asistido = any(s["asistido"] for s in ss)
            con_kg = [s for s in ss if s["kg"] is not None]
            if con_kg:
                # top = más carga; en asistidas, menos ayuda. A igualdad, más reps.
                clave = (lambda s: (-s["kg"], s["reps"] or 0)) if asistido else (lambda s: (s["kg"], s["reps"] or 0))
                top = max(con_kg, key=clave)
            else:
                top = max(ss, key=lambda s: s["reps"] or 0)
            vol = sum((s["kg"] or 0) * (s["reps_tot"] or 0) for s in ss) if not asistido else None
            rirs = [s["rir"] for s in ss if s["rir"] is not None]
            e = ejercicios.setdefault(ej, {"ej": ej, "sesion": ss[0]["sesion"], "asistido": asistido,
                                           "nombres": set(), "puntos": []})
            e["asistido"] = e["asistido"] or asistido
            e["nombres"].update(s["orig"] for s in ss)
            e["puntos"].append({
                "fecha": d["fecha"], "kg": top["kg"], "reps": top["reps"], "seg": top["seg"],
                "nota_kg": top["nota_kg"], "n": len(ss), "vol": round(vol) if vol else None,
                "rir_min": min(rirs) if rirs else None,
                "series": [[s["reps_txt"], s["kg_txt"], s["notas"]] for s in ss],
            })
    for e in ejercicios.values():
        e["nombres"] = sorted(e["nombres"])
    return ejercicios


def sin_md(t: str) -> str:
    return re.sub(r"\*\*|`|~~", "", t).strip()


def numero_es(t: str):
    """'**2.600 kcal**' → 2600, '10.000' → 10000, '170 g' → 170."""
    m = re.search(r"\d[\d.]*(?:,\d+)?", t or "")
    return float(m.group(0).replace(".", "").replace(",", ".")) if m else None


def objetivos() -> dict:
    """Tabla 'Objetivos diarios' de plan-nutricion.md → {kcal: [entreno, descanso], prot, carb, grasa, pasos}."""
    claves = {"calorias": "kcal", "proteina": "prot", "carbohidratos": "carb", "grasas": "grasa", "pasos": "pasos"}
    try:
        sec = secciones(PLAN_NUTRICION.read_text(encoding="utf-8"))
    except OSError:
        return {}
    out = {}
    for fila in sec.get("objetivos diarios", []):
        nombre = plano(sin_md(col(fila, "metrica")))
        k = next((v for c, v in claves.items() if nombre.startswith(c)), None)
        if k:
            out[k] = [numero_es(col(fila, "dia de entreno")), numero_es(col(fila, "dia de descanso"))]
    return out


def estado_sesiones() -> dict:
    """De 40 - historico/entreno/<tipo>.md: tabla "Estado actual", notas de esa sección, avisos y última fecha."""
    out = {}
    for tipo in TIPOS:
        try:
            texto = (HISTORICO / f"{tipo}.md").read_text(encoding="utf-8")
        except OSError:
            continue
        sec = secciones(texto)
        filas = next((v for k, v in sec.items() if k.startswith("estado actual")), [])
        estado = []
        for f in filas:
            nombre = sin_md(col(f, "ejercicio"))
            if nombre:
                estado.append({"ej": clasificar(nombre)[0], "nombre": nombre,
                               "carga": sin_md(col(f, "carga")), "siguiente": sin_md(col(f, "siguiente"))})
        notas, avisos, en = [], [], None
        for linea in texto.splitlines():
            if linea.startswith("## "):
                en = plano(linea[3:])
                continue
            l = linea.strip()
            if not l or l.startswith("|"):
                continue
            if en and en.startswith("estado actual"):
                notas.append(sin_md(l))
            elif en == "avisos":
                if l.startswith("- "):
                    avisos.append(sin_md(l[2:]))
                elif avisos:
                    avisos[-1] += " " + sin_md(l)
        out[tipo] = {"estado": estado, "notas": notas, "avisos": avisos, "ultima": frontmatter(texto).get("ultima")}
    return out


def construir(raiz: Path = DIARIO) -> dict:
    dias = leer_todo(raiz)
    ejercicios = resumen_ejercicios(dias)
    diarios = [{k: d.get(k) for k in ("fecha", "peso", "pasos", "kcal_consumidas", "kcal_gastadas",
                                       "kcal_basal", "kcal_actividad", "balance", "prot_total",
                                       "carb_total", "grasa_total", "sueno", "hrv", "fc_reposo", "confirmado", "cheat", "hipo",
                                       "sesiones", "avisos")} for d in dias]
    for d, orig in zip(diarios, dias):
        d["n_series"] = len(orig.get("series", []))
        d["deporte"] = [x["act"] for x in orig.get("deporte", [])]
    for d, orig in list(zip(diarios, dias))[-3:]:  # comidas solo de los últimos días (sección Hoy)
        d["comidas"] = orig.get("comidas", [])
    return {"generado": datetime.now().strftime("%Y-%m-%d %H:%M"), "dias": diarios,
            "objetivos": objetivos(), "sesiones_estado": estado_sesiones(),
            "ejercicios": sorted(ejercicios.values(), key=lambda e: (e["sesion"], -len(e["puntos"]), e["ej"]))}


def html(datos: dict, plantilla: str = "entreno.html") -> str:
    plantilla = (Path(__file__).with_name(plantilla)).read_text(encoding="utf-8")
    js = json.dumps(datos, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    return plantilla.replace("/*__DATOS__*/null", js)


if __name__ == "__main__":
    datos = construir()
    if "--json" in sys.argv:
        print(json.dumps(datos, ensure_ascii=False, indent=1))
    else:
        salida = sys.argv[sys.argv.index("--salida") + 1] if "--salida" in sys.argv else "entreno-panel.html"
        Path(salida).write_text(html(datos), encoding="utf-8")
        print(f"{salida}: {len(datos['dias'])} días, {len(datos['ejercicios'])} ejercicios")
