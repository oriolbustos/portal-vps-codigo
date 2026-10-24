"""Rutas del vault que usa el portal: todas aquí, para que un cambio de carpetas se arregle en un solo sitio.

Estructura (2026-10-23): dominios `NN - EnglishName` en la raíz del vault. Lo que empieza por `_`
(`_context.md`, `_history.md`, `_system/`...) es del sistema: los listados del portal lo ignoran (ver `visible`).
Los escritores `*-guardar.py` corren como root y NO importan este módulo: llevan sus rutas escritas a mano.
"""
from pathlib import Path

VAULT = Path("/srv/vault")

# 10 - NTT Data y 20 - Career (Trabajo)
NTT = VAULT / "10 - NTT Data"
CARRERA = VAULT / "20 - Career"
OPORTUNIDADES = CARRERA / "Opportunities"
CVS = OPORTUNIDADES / "CVs"
EDUCACION = CARRERA / "Education"
PROYECTOS = CARRERA / "Projects"

# 30 - Health (Entreno; medical/ y appearance/ guardan además las notas de ámbito Health y Aspecto de Tareas)
SALUD = VAULT / "30 - Health"
ENTRENO = SALUD
DIARIO = ENTRENO / "diary"                                   # AAAA/MM/AAAA-MM-DD.md (+ _mes.md)
CHECKINS = ENTRENO / "checkins"                              # AAAA-MM-DD.md
REFERENCIA = ENTRENO / "reference"
PLAN_NUTRICION = REFERENCIA / "plan-nutricion.md"
MUSCULOS = REFERENCIA / "musculos.md"
CONSTANTES = REFERENCIA / "datos-y-constantes.md"
PROGRESO = ENTRENO / "progress"                              # push.md, pull.md, pierna.md: "Estado actual"
PLANTILLAS_ENTRENO = ENTRENO / "_system" / "templates"
PLANTILLA_DIARIO = PLANTILLAS_ENTRENO / "diario.md"
PLANTILLA_CHECKIN = PLANTILLAS_ENTRENO / "checkin.md"

# 40 - Life (Tareas y To Do)
VIDA = VAULT / "40 - Life"
PANEL = VIDA / "Panel.md"
AMBITOS = VIDA / "areas"
# Dónde busca tareas.py las notas de ámbito que enlaza Panel.md ([[Health]], [[Aspecto]]...), por orden.
# Copia en tareas-guardar.py (root): si cambias esto, cambia también allí.
RAICES_AMBITOS = (AMBITOS, SALUD / "medical", SALUD / "appearance")
TODO = VIDA / "To Do.md"

# 60 - Lists (Listas)
LISTAS = VAULT / "60 - Lists"

# 70 - System (Sistema, estilo)
SISTEMA = VAULT / "70 - System"
LATIDO = SISTEMA / "status.md"                               # lo escribe /opt/salud/check.sh
ESTILO = SISTEMA / "estilo-frontend.md"


def visible(p: Path) -> bool:
    """False para archivos y carpetas de sistema (nombre que empieza por "_")."""
    return not p.name.startswith("_")
