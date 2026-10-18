#!/bin/bash
DOMAIN="testobm.duckdns.org"
TOKEN=$(grep ^MCP_VAULT_RUTA= /opt/salud/.env | cut -d= -f2)   # ruta secreta del MCP, en el .env
LOG="/srv/vault/30 - Personal/estado-sistema.md"
HC_URL=""   # se rellena en el paso 3

FALLOS=""

# Contenedores vivos (y reintento de arranque si alguno cayó)
for c in mcp caddy obsidian; do
  estado=$(docker inspect -f '{{.State.Status}}' salud-$c-1 2>/dev/null)
  if [ "$estado" != "running" ]; then
    FALLOS="$FALLOS $c:caído"
    docker compose -f /opt/salud/docker-compose.yml up -d $c
  fi
done

# La app Obsidian vive de verdad dentro del contenedor (no solo el contenedor "Up")
docker exec salud-obsidian-1 pgrep -f /opt/obsidian/obsidian >/dev/null 2>&1 || FALLOS="$FALLOS obsidian-app:muerta"

# El conector responde por la URL pública (el mismo camino que usa Claude)
code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "https://$DOMAIN/$TOKEN/mcp" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}')
if [ "$code" != "200" ]; then
  docker compose -f /opt/salud/docker-compose.yml restart mcp
  sleep 20
  code=$(curl -s -o /dev/null -w '%{http_code}' -m 15 -X POST "https://$DOMAIN/$TOKEN/mcp" \
    -H 'Content-Type: application/json' \
    -H 'Accept: application/json, text/event-stream' \
    --data '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"check","version":"1"}}}')
  if [ "$code" != "200" ]; then
    FALLOS="$FALLOS conector-http:$code(reinicio-fallido)"
  else
    FALLOS="$FALLOS mcp:auto-relevantado"
  fi
fi

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
