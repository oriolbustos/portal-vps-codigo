#!/bin/bash
# Crea o cambia la contraseña de un explorador del portal: vault (/vault/) o sistema (/sistema/archivos/).
# Uso: /opt/portal/cambiar-clave-archivos.sh vault|sistema
# Es distinta de la del portal y de la caja de finanzas. Cerrará ese explorador en todos los dispositivos.
set -e
case "$1" in vault|sistema) ;; vps) set -- sistema ;; *) echo "Uso: $0 vault|sistema"; exit 1 ;; esac
F="/etc/portal/clave-$1"
read -r -s -p "Nueva contraseña de $1: " P; echo
read -r -s -p "Repítela: " P2; echo
[ "$P" = "$P2" ] || { echo "No coinciden, no cambio nada."; exit 1; }
[ ${#P} -ge 10 ] || { echo "Mínimo 10 caracteres."; exit 1; }
[ "$(printf %s "$P" | wc -c)" -le 72 ] || { echo "Máximo 72 bytes (límite de bcrypt)."; exit 1; }
cmp -s <(printf %s "$P" | python3 -c 'import bcrypt,sys; h=open("/etc/portal/clave","rb").read().strip(); print(bcrypt.checkpw(sys.stdin.buffer.read(), h))') <(echo True) && { echo "Es la misma que la del portal. Elige otra."; exit 1; }
printf %s "$P" | python3 -c 'import bcrypt,sys; sys.stdout.write(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(12)).decode())' > "$F.nueva"
chown root:portal "$F.nueva" && chmod 640 "$F.nueva"
mv "$F.nueva" "$F"
python3 - "$1" <<'Q'
import json, sys
r = {"vault": "vault", "sistema": "vps"}[sys.argv[1]]
f = "/var/lib/portal/sesiones.json"
try:
    s = json.load(open(f))
except Exception:
    sys.exit()
for v in s.values():
    v.pop(f"abierto_{r}", None)
json.dump(s, open(f, "w"))
Q
echo "Hecho. Contraseña de $1 guardada en $F."
