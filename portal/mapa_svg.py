"""Genera el SVG del Mapa de Sistema (posiciones fijas). Uso: python3 mapa_svg.py /tmp/mapa.svg y pegar el resultado dentro de <div class="diagrama" id="diagrama"> en sistema.html."""
import sys

W, H = 1000, 640
N = {}  # id -> (x, y, w, h)
out = []


def nodo(i, x, y, w, h, titulo, sub="", dato=False):
    N[i] = (x, y, w, h)
    cls = "n dato" if dato else "n"
    r = 14 if dato else 0
    out.append(f'<g class="{cls}"><rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{r}"/>'
               f'<text class="t" x="{x + 10}" y="{y + (h / 2 - 2 if sub else h / 2 + 5)}">{titulo}</text>'
               + (f'<text class="s" x="{x + 10}" y="{y + h / 2 + 14}">{sub}</text>' if sub else "") + "</g>")


def p(i, lado, f=0.5):
    x, y, w, h = N[i]
    return {"d": (x + w, y + h * f), "i": (x, y + h * f), "a": (x + w * f, y), "b": (x + w * f, y + h)}[lado]


aristas = []


def arista(a, b, tipo, etiqueta="", fa=0.5, fb=0.5, la="d", lb="i", via=None, et_pos=0.5):
    (x1, y1), (x2, y2) = p(a, la, fa), p(b, lb, fb)
    if via:
        d = f"M{x1},{y1} C{via[0]},{via[1]} {via[2]},{via[3]} {x2},{y2}"
        mx, my = (x1 + 3 * via[0] + 3 * via[2] + x2) / 8, (y1 + 3 * via[1] + 3 * via[3] + y2) / 8
    else:
        cx = (x1 + x2) / 2
        d = f"M{x1},{y1} C{cx},{y1} {cx},{y2} {x2},{y2}"
        mx, my = x1 + (x2 - x1) * et_pos, y1 + (y2 - y1) * et_pos
    aristas.append(f'<path class="e {tipo}" d="{d}" marker-end="url(#f-{tipo})"/>')
    if etiqueta:
        aristas.append(f'<text class="el" x="{mx}" y="{my - 4}" text-anchor="middle">{etiqueta}</text>')


# marcos
marcos = [
    '<rect class="m" x="8" y="10" width="200" height="620"/><text class="mt" x="18" y="30">QUIÉN ENTRA</text>',
    '<rect class="m vps" x="232" y="10" width="610" height="620"/><text class="mt" x="242" y="30">VPS HETZNER · TESTOBM.DUCKDNS.ORG</text>',
    '<text class="mt" x="262" y="62">ENTRADA</text><text class="mt" x="472" y="62">SERVICIOS</text><text class="mt" x="702" y="62">DATOS</text>',
    '<rect class="m" x="858" y="10" width="134" height="620"/><text class="mt" x="868" y="30">FUERA</text>',
]
# nodos
nodo("iphone", 20, 80, 176, 54, "iPhone", "Obsidian, portal, pasos")
nodo("mac", 20, 170, 176, 54, "Mac del trabajo", "Obsidian, Claude Code")
nodo("lenovo", 20, 260, 176, 54, "Lenovo", "Obsidian, Claude Code")
nodo("claude", 20, 400, 176, 54, "Claude (Anthropic)", "app, web, Remote Control")
nodo("caddy", 256, 150, 150, 70, "Caddy", "HTTPS, login")
nodo("ssh", 256, 290, 150, 54, "SSH", "clave: root y claude")
svc = [("portal", "Portal", "paneles"), ("webdav", "WebDAV", "sync de Obsidian"), ("mcp", "MCP", "vault y Finanzas"),
       ("llave", "Llave Finanzas", "abre la caja"), ("cc", "Claude Code", "usuario claude"),
       ("sync", "salud-sync", "10:30 y 21:30"), ("cron", "Cron", "latido y backup"), ("obs", "Obsidian", "puente con Sync")]
for k, (i, t, s) in enumerate(svc):
    nodo(i, 466, 76 + 68 * k, 176, 50, t, s)
nodo("vault", 696, 120, 134, 120, "Vault", "fuente de verdad", dato=True)
nodo("caja", 696, 300, 134, 70, "Caja Finanzas", "cifrada", dato=True)
nodo("fitbit", 868, N["sync"][1], 116, 50, "Fitbit", "datos del reloj")
nodo("github", 868, N["cron"][1], 116, 50, "GitHub", "código y backup")
nodo("osync", 868, N["obs"][1], 116, 50, "Obsidian Sync", "hasta noviembre")

# aristas
arista("iphone", "caddy", "https", "HTTPS", fb=0.2, et_pos=0.35)
arista("mac", "caddy", "https", fb=0.4)
arista("lenovo", "caddy", "https", fb=0.6)
arista("claude", "caddy", "https", "conectores", fb=0.85, et_pos=0.4)
arista("mac", "ssh", "ssh", "ssh", fa=0.75, fb=0.3, et_pos=0.6)
arista("lenovo", "ssh", "ssh", fa=0.75, fb=0.6)
for k, i in enumerate(["portal", "webdav", "mcp", "llave"]):
    arista("caddy", i, "int", fa=0.2 + 0.2 * k)
arista("ssh", "cc", "int", "shell", et_pos=0.5)
arista("portal", "vault", "int", "escritores", fb=0.15, et_pos=0.45)
arista("webdav", "vault", "int", fb=0.32)
arista("mcp", "vault", "int", fb=0.5)
arista("mcp", "caja", "int", fa=0.75, fb=0.3)
arista("llave", "caja", "int", fb=0.65)
arista("cc", "vault", "int", fb=0.68)
arista("sync", "vault", "int", fb=0.82)
arista("obs", "vault", "int", fb=0.95)
arista("cc", "claude", "sal", "Remote Control", la="i", lb="d", fa=0.7, fb=0.7,
       via=(380, N["cc"][1] + 70, 260, N["claude"][1] + 40))
arista("sync", "fitbit", "sal", "lee", fa=0.35, fb=0.35, et_pos=0.75)
arista("cron", "github", "sal", "backup", et_pos=0.75)
arista("obs", "osync", "temp", "puente", et_pos=0.75)

defs = "<defs>" + "".join(
    f'<marker id="f-{t}" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
    f'<path class="fl {t}" d="M0,0 L8,4 L0,8 z"/></marker>' for t in ("https", "ssh", "int", "sal", "temp")) + "</defs>"
svg = (f'<svg class="mapa-svg" viewBox="0 0 {W} {H}" role="img" aria-label="Mapa del sistema: quién entra, servicios del VPS, datos y servicios de fuera">'
       + defs + "".join(marcos) + "".join(aristas) + "".join(out) + "</svg>")
open(sys.argv[1] if len(sys.argv) > 1 else "mapa.svg", "w").write(svg)
print(len(svg))
