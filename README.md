# Código del VPS

Código del servidor personal (`ssh vps`). Vive en `/opt`; systemd y cron apuntan aquí directamente.

| Carpeta | Qué |
|---|---|
| `portal/` | Portal web (servicio `portal`, puerto 8791) y escritores root `*-guardar` |
| `finanzas/` | Llave de la caja de Finanzas (servicio `finanzas-llave`, puerto 8790) |
| `salud-sync/` | Google Health / Fitbit y pasos del iPhone → diario de Entreno |
| `salud/` | Docker (Caddy, MCP, WebDAV, Obsidian), latido (`check.sh`) y backup del vault |

Las unidades systemd están aquí y `/etc/systemd/system/` tiene enlaces a ellas.
Los secretos están en `/opt/salud/.env`, `/etc/portal/` y `/etc/salud-sync/`, fuera del repo.
Los datos están en el vault (`/srv/vault`) y en `/var/lib/portal`.

Documentación completa: nota `acceso-vps` del vault.
