#!/bin/bash
# Copia completa del servidor para guardar en los discos de Oriol (Z:).
# Escribe un .tar.gz por la salida estándar; no deja nada en el disco del VPS.
# Lo lanza el PC: backup-vps.bat en Z:/oriol/personal/backups/vps/ (ssh vps /opt/salud/backup-local.sh > ...).
# Qué entra y por qué: "70 - System/backup-local.md" del vault. Si añades algo aquí, apúntalo allí.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "ejecutar como root" >&2; exit 1; }

meta=$(mktemp -d)
trap 'rm -rf "$meta"' EXIT

# Lo que no son archivos sueltos: se guarda como texto en _meta/
{
  echo "Copia del VPS $(hostname) del $(date '+%F %T %Z')"
  echo "IP pública: $(curl -s -4 --max-time 5 ifconfig.me || echo '?')"
  echo "Commit de /opt: $(git -C /opt log -1 --format='%h %ci %s')"
} > "$meta/LEEME.txt"
crontab -l > "$meta/crontab-root.txt"
ls -la /etc/systemd/system/ > "$meta/systemd-system.txt"
docker ps -a --format '{{.Names}}\t{{.Image}}\t{{.Status}}' > "$meta/docker.txt" 2>/dev/null || true
cp /etc/ssh/sshd_config "$meta/" 2>/dev/null || true
cp -r /etc/ssh/sshd_config.d "$meta/" 2>/dev/null || true

# Rutas absolutas (sin la / inicial) para que la copia restaure en su sitio.
cd /
tar -czf - \
  --exclude='opt/salud/obsidian-config' \
  --exclude='__pycache__' \
  --exclude='root/.gnupg/S.*' \
  --exclude='srv/vault/.trash' \
  --exclude='srv/vault/.obsidian/workspace*.json' \
  --exclude='var/lib/portal/sesiones.json' \
  --exclude='var/lib/portal/*-pendiente' \
  --exclude='var/lib/portal/*-resultado.json' \
  srv/vault \
  opt \
  etc/portal \
  etc/salud-sync \
  var/lib/portal \
  var/lib/salud-sync \
  srv/entreno-fotos \
  srv/finanzas-cifrado \
  root/.gnupg \
  root/.ssh \
  home/claude/.config/systemd \
  home/claude/.claude/CLAUDE.md \
  home/claude/.claude/settings.json \
  -C "$meta" --transform 's,^\./,_meta/,' .
