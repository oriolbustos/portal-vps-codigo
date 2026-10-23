#!/bin/bash
set -euo pipefail
# Estado del portal que no vive en notas (pasos del iPhone, configuración del modo censurado): entra en el backup.
# Restaurar: copiar de /srv/vault/.portal-estado/ a /var/lib/portal/ (dueño portal:portal, permisos 600).
mkdir -p /srv/vault/.portal-estado
cp /var/lib/portal/pasos-iphone.json /srv/vault/.portal-estado/
cp /var/lib/portal/config.json /srv/vault/.portal-estado/ 2>/dev/null || true  # modo censurado (/config/)
cp /var/lib/portal/apariencia.json /srv/vault/.portal-estado/ 2>/dev/null || true  # apariencia y portada (/config/)
rm -rf /srv/vault/.portal-estado/apariencia; cp -r /var/lib/portal/apariencia /srv/vault/.portal-estado/ 2>/dev/null || true  # iconos y fuentes subidos
chown -R 1000:1000 /srv/vault/.portal-estado
export GIT_DIR=/srv/vault-backup.git GIT_WORK_TREE=/srv/vault
cd /srv/vault
git add -A
git diff --cached --quiet || git commit -q -m "auto: $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "$(date '+%F %T') ok"
