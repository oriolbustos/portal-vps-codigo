#!/bin/bash
set -euo pipefail
# Estado del portal que no vive en notas (pasos del iPhone, configuración del modo censurado): entra en el backup.
# Restaurar: copiar de /srv/vault/.portal-estado/ a /var/lib/portal/ (dueño portal:portal, permisos 600).
mkdir -p /srv/vault/.portal-estado
cp /var/lib/portal/pasos-iphone.json /srv/vault/.portal-estado/
cp /var/lib/portal/config.json /srv/vault/.portal-estado/ 2>/dev/null || true  # modo censurado (/config/)
chown -R 1000:1000 /srv/vault/.portal-estado
cd /srv/vault
git add -A
git diff --cached --quiet || git commit -q -m "auto: $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "$(date '+%F %T') ok"
