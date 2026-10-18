#!/bin/bash
set -euo pipefail
# Estado del portal que no vive en notas (urgente/importante de Tareas, pasos del iPhone): entra en el backup.
# Restaurar: copiar de /srv/vault/.portal-estado/ a /var/lib/portal/ (dueño portal:portal, permisos 600).
mkdir -p /srv/vault/.portal-estado
cp /var/lib/portal/tareas.json /var/lib/portal/pasos-iphone.json /srv/vault/.portal-estado/
chown -R 1000:1000 /srv/vault/.portal-estado
cd /srv/vault
git add -A
git diff --cached --quiet || git commit -q -m "auto: $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "$(date '+%F %T') ok"
