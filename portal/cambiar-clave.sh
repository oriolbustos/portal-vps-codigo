#!/bin/bash
# Cambia la contraseña del portal https://testobm.duckdns.org/ (usuario oriol).
# Cierra todas las sesiones abiertas: cada dispositivo tendrá que volver a entrar.
# (La contraseña de la caja de finanzas es otra y no cambia aquí.)
set -e
read -r -s -p "Nueva contraseña del portal: " P; echo
read -r -s -p "Repítela: " P2; echo
[ "$P" = "$P2" ] || { echo "No coinciden, no cambio nada."; exit 1; }
[ ${#P} -ge 10 ] || { echo "Mínimo 10 caracteres."; exit 1; }
[ "$(printf %s "$P" | wc -c)" -le 72 ] || { echo "Máximo 72 bytes (límite de bcrypt)."; exit 1; }
cp -p /etc/portal/clave "/etc/portal/clave.bak-$(date +%Y%m%d-%H%M)"
printf %s "$P" | python3 -c 'import bcrypt,sys; sys.stdout.write(bcrypt.hashpw(sys.stdin.buffer.read(), bcrypt.gensalt(12)).decode())' > /etc/portal/clave.nueva
chown root:portal /etc/portal/clave.nueva && chmod 640 /etc/portal/clave.nueva
mv /etc/portal/clave.nueva /etc/portal/clave
rm -f /var/lib/portal/sesiones.json
systemctl restart portal
echo "Hecho. Usuario: oriol. Sesiones cerradas en todos los dispositivos."
