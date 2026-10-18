#!/bin/bash
# Genera un token nuevo para el Atajo de pasos del iPhone (el viejo deja de valer)
# y lo enseña para pegarlo en el Atajo (cabecera Authorization: "Bearer <token>").
set -e
python3 -c "import secrets;print(secrets.token_urlsafe(32))" > /etc/portal/pasos-token
chown root:portal /etc/portal/pasos-token; chmod 640 /etc/portal/pasos-token
echo "Token nuevo. En el Atajo, cabecera Authorization = Bearer $(cat /etc/portal/pasos-token)"
