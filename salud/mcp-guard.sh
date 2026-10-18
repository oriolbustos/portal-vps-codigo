#!/bin/bash
P=$(docker stats --no-stream --format '{{.PIDs}}' salud-mcp-1 2>/dev/null)
if [ -n "$P" ] && [ "$P" -gt 400 ]; then
  cd /opt/salud && docker compose restart mcp
  echo "$(date '+%F %T') mcp reiniciado con $P pids" >> /var/log/mcp-guard.log
fi
