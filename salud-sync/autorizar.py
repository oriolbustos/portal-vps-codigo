#!/usr/bin/env python3
"""Autoriza al VPS a leer tus datos de Google Health (Fitbit) una sola vez.

Uso (en el VPS, como root):  python3 /opt/salud-sync/autorizar.py
Pide el Client ID y el Client Secret del proyecto de Google Cloud, te da un enlace,
lo abres, aceptas y pegas aquí la dirección a la que te manda Google (https://www.google.com/?code=...).
Guarda todo en /etc/salud-sync/google.json (solo root). Volver a ejecutarlo renueva el permiso.
"""
import getpass, json, os, sys, urllib.error, urllib.parse, urllib.request
from pathlib import Path

DESTINO = Path("/etc/salud-sync/google.json")
REDIRECT = "https://www.google.com"
SCOPES = [
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    "https://www.googleapis.com/auth/googlehealth.sleep.readonly",
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]

previo = json.loads(DESTINO.read_text()) if DESTINO.exists() else {}
cid = input(f"Client ID [{'el guardado' if previo.get('client_id') else 'ninguno'}]: ").strip() or previo.get("client_id", "")
secreto = getpass.getpass("Client Secret (no se ve al escribir; Intro = el guardado): ").strip() or previo.get("client_secret", "")
if not cid or not secreto:
    sys.exit("Faltan el Client ID o el Secret.")

url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": cid, "redirect_uri": REDIRECT, "response_type": "code", "scope": " ".join(SCOPES),
    "access_type": "offline", "prompt": "consent",
})
print("\n1) Abre este enlace en el navegador, entra con tu cuenta de Google (la de Fitbit) y acepta:\n")
print(url)
print("\n2) Google te mandará a www.google.com. Copia la dirección COMPLETA de la barra y pégala aquí.")
vuelta = input("\nDirección: ").strip()
codigo = urllib.parse.parse_qs(urllib.parse.urlsplit(vuelta).query).get("code", [vuelta])[0]

datos = urllib.parse.urlencode({"code": codigo, "client_id": cid, "client_secret": secreto,
                                "redirect_uri": REDIRECT, "grant_type": "authorization_code"}).encode()
try:
    r = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", datos, timeout=30))
except urllib.error.HTTPError as e:
    sys.exit(f"Google ha rechazado el código: {e.read().decode()[:300]}")
if "refresh_token" not in r:
    sys.exit("Google no ha devuelto refresh_token. Quita el acceso en myaccount.google.com/permissions y repite.")

DESTINO.parent.mkdir(mode=0o700, exist_ok=True)
tmp = DESTINO.with_suffix(".tmp")
tmp.write_text(json.dumps({"client_id": cid, "client_secret": secreto, "refresh_token": r["refresh_token"],
                           "scopes": r.get("scope", "")}, indent=1))
os.chmod(tmp, 0o600)
tmp.replace(DESTINO)
print(f"\nHecho. Permisos concedidos: {r.get('scope', '?')}")
print(f"Guardado en {DESTINO} (solo root).")
