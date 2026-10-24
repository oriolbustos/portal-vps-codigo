#!/bin/bash
MCP_HOST="mcp.zac2zac.com"; DAV_HOST="dav.zac2zac.com"
# Directo al Caddy local (no por Cloudflare)
SECRETO=$(grep ^ORIGEN_SECRETO= /opt/salud/.env | cut -d= -f2)   # Caddy exige la cabecera que pone Cloudflare
LOCAL=(--resolve "$MCP_HOST:443:127.0.0.1" --resolve "mcp-fin.zac2zac.com:443:127.0.0.1" --resolve "$DAV_HOST:443:127.0.0.1" -H "X-Origen-Secreto: $SECRETO")
# Los MCP van detrás del login OAuth: se prueban por dentro, directos al contenedor (sin login)
mcp_init() {  # $1 = contenedor; imprime el código HTTP
  ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' salud-$1-1 2>/dev/null)
  curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "http://$ip:8000/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}'
}
LOG="/srv/vault/70 - System/status.md"
HC_URL=""   # se rellena en el paso 3

FALLOS=""

# Contenedores vivos (y reintento de arranque si alguno cayó)
for c in mcp mcp-finanzas mcp-auth mcp-fin-auth webdav caddy obsidian; do
  estado=$(docker inspect -f '{{.State.Status}}' salud-$c-1 2>/dev/null)
  if [ "$estado" != "running" ]; then
    FALLOS="$FALLOS $c:caído"
    docker compose -f /opt/salud/docker-compose.yml up -d $c
  fi
done

# La app Obsidian vive de verdad dentro del contenedor (no solo el contenedor "Up")
docker exec salud-obsidian-1 pgrep -f /opt/obsidian/obsidian >/dev/null 2>&1 || FALLOS="$FALLOS obsidian-app:muerta"

# El MCP del vault responde
code=$(mcp_init mcp)
if [ "$code" != "200" ]; then
  docker compose -f /opt/salud/docker-compose.yml restart mcp
  sleep 20
  code=$(mcp_init mcp)
  if [ "$code" != "200" ]; then
    FALLOS="$FALLOS conector-http:$code(reinicio-fallido)"
  else
    FALLOS="$FALLOS mcp:auto-relevantado"
  fi
fi

# WebDAV (rclone, lo usa Remotely Save): sin credenciales debe contestar 401 (vivo y exigiendo login)
code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${LOCAL[@]}" -X PROPFIND "https://$DAV_HOST/webdav/")
[ "$code" = "401" ] || FALLOS="$FALLOS webdav-http:$code"

# MCP de finanzas responde
code=$(mcp_init mcp-finanzas)
[ "$code" = "200" ] || FALLOS="$FALLOS mcp-finanzas-http:$code"

# Los logins OAuth están delante: sin token deben contestar 401 (vivos y exigiendo login)
for h in mcp mcp-fin; do
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 "${LOCAL[@]}" -X POST "https://$h.zac2zac.com/mcp")
  [ "$code" = "401" ] || FALLOS="$FALLOS $h-auth-http:$code"
done

# Disco
uso=$(df / --output=pcent | tail -1 | tr -dc '0-9')
[ "$uso" -gt 85 ] && FALLOS="$FALLOS disco:${uso}%"

# Latido: escribe el resultado en el vault
{
  echo "---"
  echo "actualizado: $(date '+%Y-%m-%d %H:%M')"
  echo "estado: ${FALLOS:-OK}"
  echo "disco: ${uso}%"
  echo "---"
  echo ""
  echo "Si 'actualizado' tiene más de 20 min, algo falla (VPS, script o Sync)."
} > "$LOG"
chown 1000:1000 "$LOG"

# Aviso al vigilante externo solo si todo está OK
[ -z "$FALLOS" ] && [ -n "$HC_URL" ] && curl -fsS -m 10 "$HC_URL" >/dev/null
