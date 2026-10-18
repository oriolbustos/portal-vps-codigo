#!/bin/bash
set -euo pipefail
cd /srv/vault
git add -A
git diff --cached --quiet || git commit -q -m "auto: $(date '+%Y-%m-%d %H:%M')"
git push -q origin main
echo "$(date '+%F %T') ok"
