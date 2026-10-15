#!/usr/bin/env python3
"""Zona Mixta CRC: genera la edición diaria con titulares de RSS (sin IA, sin costo)."""
import html
from functools import lru_cache
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

import tiempo as imn

NOMBRE = "Zona Mixta CRC"
BASE = "https://jrochab.github.io/boletin-noticias-crc"
TZ = ZoneInfo("America/Costa_Rica")
ROOT = Path(__file__).resolve().parent
UA = "Mozilla/5.0 (compatible; ZonaMixtaCRC/1.0; +https://github.com/jrochab/boletin-noticias-crc)"

# ---------------------------------------------------------------------------
# Fuentes. Si un enlace deja de funcionar, el script lo omite y sigue con los demás.
# Para agregar o quitar medios, edite solo esta lista.
# ---------------------------------------------------------------------------
SECCIONES = [
    # Se priorizan medios y agencias con más posibilidades de lectura gratuita.
    # Google Noticias funciona como agregador cuando el medio no ofrece RSS estable.
    {"id": "costa-rica", "titulo": "Costa Rica", "max": 8, "feeds": [
        ("CRHoy", "https://www.crhoy.com/feed/"),
        ("Diario Extra", "https://news.google.com/rss/search?q=site%3Adiarioextra.com&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("La Teja", "https://news.google.com/rss/search?q=site%3Alateja.cr&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("Teletica", "https://news.google.com/rss/search?q=site%3Ateletica.com&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("La República", "https://news.google.com/rss/search?q=site%3Alarepublica.net&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("El Financiero", "https://www.elfinancierocr.com/arc/outboundfeeds/rss/?outputType=xml"),
        ("Observador", "https://observador.cr/feed/"),
        ("La Nación", "https://www.nacion.com/arc/outboundfeeds/rss/?outputType=xml"),
    ]},
    {"id": "deportes", "titulo": "Deportes", "max": 5, "feeds": [
        ("CRHoy Deportes", "https://www.crhoy.com/deportes/feed/"),
        ("Diario Extra Deportes", "https://news.google.com/rss/search?q=site%3Adiarioextra.com+deportes&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("La Teja Deportes", "https://news.google.com/rss/search?q=site%3Alateja.cr+deportes&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("Observador Deportes", "https://observador.cr/category/deportes/feed/"),
        ("La Nación Deportes", "https://www.nacion.com/arc/outboundfeeds/rss/category/puro-deporte/?outputType=xml"),
    ]},
    {"id": "cultura", "titulo": "Cultura", "max": 3, "feeds": [
        ("DW Cultura", "https://rss.dw.com/xml/rss-sp-cul"),
        ("France 24", "https://www.france24.com/es/rss"),
        ("La Nación Viva", "https://www.nacion.com/arc/outboundfeeds/rss/category/viva/?outputType=xml"),
    ]},
    {"id": "tecnologia", "titulo": "Tecnología", "max": 4, "feeds": [
        ("Xataka", "https://feeds.weblogssl.com/xataka2"),
        ("Hipertextual", "https://hipertextual.com/feed"),
        ("DW Tecnología", "https://rss.dw.com/xml/rss-sp-all"),
    ]},
    {"id": "internacional", "titulo": "Actualidad internacional", "max": 6, "feeds": [
        ("France 24", "https://www.france24.com/es/rss"),
        ("DW", "https://rss.dw.com/xml/rss-sp-all"),
        ("BBC Mundo", "https://feeds.bbci.co.uk/mundo/rss.xml"),
        ("EFE", "https://news.google.com/rss/search?q=site%3Aefe.com+noticias&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("AFP", "https://news.google.com/rss/search?q=site%3Aafp.com%2Fes+actualidad&hl=es-419&gl=CR&ceid=CR%3Aes-419"),
        ("Euronews", "https://es.euronews.com/rss?level=theme&name=news"),
    ]},
]

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
         "septiembre", "octubre", "noviembre", "diciembre"]


def fecha_larga(d):
    t = f"{DIAS[d.weekday()]}, {d.day} de {MESES[d.month - 1]} de {d.year}"
    return t[0].upper() + t[1:]


def esc(s):
    return html.escape(s or "", quote=True)


# ---------------------------------------------------------------------------
# Lectura de RSS / Atom
# ---------------------------------------------------------------------------
def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def limpiar(t):
    t = re.sub(r"<[^>]+>", " ", t or "")
    t = html.unescape(t)
    return re.sub(r"\s+", " ", t).strip()


def recortar(t, n=220):
    if len(t) <= n:
        return t
    corte = t[:n].rsplit(" ", 1)[0].rstrip(" ,;:.")
    return corte + "…"


def parse_fecha(txt):
    if not txt:
        return None
    txt = txt.strip()
    try:
        d = parsedate_to_datetime(txt)
    except Exception:
        try:
            d = datetime.fromisoformat(txt.replace("Z", "+00:00"))
        except Exception:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def parse_feed(data, fuente):
    root = ET.fromstring(data)
    items = []
    for nodo in root.iter():
        if nodo.tag.split("}")[-1] not in ("item", "entry"):
            continue
        hijos = {}
        for c in nodo:
            hijos.setdefault(c.tag.split("}")[-1], c)
        titulo = limpiar(hijos["title"].text) if "title" in hijos else ""
        link = ""
        if "link" in hijos:
            link = (hijos["link"].text or hijos["link"].get("href") or "").strip()
        desc = ""
        for k in ("description", "summary", "content"):
            if k in hijos and hijos[k].text:
                desc = limpiar(hijos[k].text)
                break
        fecha = None
        for k in ("pubDate", "published", "updated", "date"):
            if k in hijos and hijos[k].text:
                fecha = parse_fecha(hijos[k].text)
                if fecha:
                    break
        if not titulo or not link.startswith(("http://", "https://")):
            continue
        if desc.lower().startswith(titulo.lower()[:40]) or len(desc) < 40:
            desc = ""
        items.append({"titulo": titulo, "link": link, "resumen": recortar(desc),
                      "fecha": fecha, "fuente": fuente})
    return items


def palabras(t):
    return {w for w in re.findall(r"\w+", t.lower()) if len(w) > 3}


def duplicado(titulo, vistos):
    a = palabras(titulo)
    for b in vistos:
        if a and b and len(a & b) / len(a | b) >= 0.5:
            return True
    return False


def reunir(sec, ahora, vistos):
    listas = []
    for fuente, url in sec["feeds"]:
        try:
            items = parse_feed(fetch(url), fuente)
        except Exception as e:
            print(f"  ! {fuente} no disponible: {e}", file=sys.stderr)
            continue
        epoca = datetime.min.replace(tzinfo=timezone.utc)
        items.sort(key=lambda x: x["fecha"] or epoca, reverse=True)
        recientes = [i for i in items if i["fecha"] and i["fecha"] >= ahora - timedelta(hours=40)]
        listas.append(recientes or items[:2])
        print(f"  {fuente}: {len(items)} notas ({len(recientes)} recientes)")
    elegidos = []
    while len(elegidos) < sec["max"] and any(listas):
        for lista in listas:
            if lista and len(elegidos) < sec["max"]:
                it = lista.pop(0)
                if not duplicado(it["titulo"], vistos):
                    vistos.append(palabras(it["titulo"]))
                    elegidos.append(it)
    return elegidos


# ---------------------------------------------------------------------------
# Evangelio, santoral y mensaje
# ---------------------------------------------------------------------------
LIBROS = {"Mt": "Mateo", "Mc": "Marcos", "Lc": "Lucas", "Jn": "Juan"}


def _verso(v):
    return re.sub(r"\s+", "", v).replace("–", "-").replace(".", "")


def ref_evangelio(txt):
    """Busca la cita del Evangelio en el texto de una página. Devuelve p. ej. 'Mateo 22, 1-14'."""
    # "Evangelio del domingo 11 de octubre de 2026 (Mt 22, 1-14)"
    m = re.search(r"Evangelio[^()]{0,90}\(\s*(Mt|Mc|Lc|Jn)\s*(\d{1,2})\s*[,:]\s*([\d\-–\.y ]{1,24}?)\s*\)", txt)
    if m:
        return f"{LIBROS[m.group(1)]} {m.group(2)}, {_verso(m.group(3))}"
    # "Evangelio Lucas 11:42-46" / "Evangelio según san Mateo 22, 1-14"
    m = re.search(r"Evangelio\s+(?:seg[úu]n\s+(?:san\s+)?)?(Mateo|Marcos|Lucas|Juan)\s+(\d{1,2})\s*[,:]\s*(\d{1,2}[a-d]?(?:\s*[-–]\s*\d{1,2}[a-d]?)?)", txt, re.I)
    if m:
        return f"{m.group(1).capitalize()} {m.group(2)}, {_verso(m.group(3))}"
    # "Evangelio Mt 22, 1-14"
    m = re.search(r"Evangelio\s+(Mt|Mc|Lc|Jn)\s*(\d{1,2})\s*[,:]\s*(\d{1,2}[a-d]?(?:\s*[-–]\s*\d{1,2}[a-d]?)?)", txt)
    if m:
        return f"{LIBROS[m.group(1)]} {m.group(2)}, {_verso(m.group(3))}"
    return None


def _meta(crudo, nombre):
    """Contenido de <meta name|property="nombre" content="...">."""
    for tag in re.findall(r"<meta\b[^>]*>", crudo, re.I):
        attrs = {k.lower(): v for k, v in re.findall(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag)}
        if attrs.get("name") == nombre or attrs.get("property") == nombre:
            return limpiar(attrs.get("content", ""))
    return None


def _dominicos(d, crudo):
    """Datos de dominicos.org (página 'hoy'). Solo vale si la página es de la fecha pedida."""
    desc = _meta(crudo, "description") or ""
    m = re.match(r"\s*(\d{1,2})\s+(\w+)\.?\s+(\d{4})\s*,\s*(.+?)\.\s+Evangelio del d", desc)
    if not m:
        return None
    if int(m.group(1)) != d.day or int(m.group(3)) != d.year or not MESES[d.month - 1].startswith(m.group(2).lower()[:3]):
        print("  ! Dominicos: la página 'hoy' no es de la fecha pedida; se ignora")
        return None
    ref = ref_evangelio(re.sub(r"Evangelio del d[ií]a seg[úu]n (?:san )?", "Evangelio ", desc))
    dia = m.group(4).strip()
    lema = _meta(crudo, "og:title")
    video = re.search(r"https://www\.youtube\.com/watch\?v=[\w-]{6,20}", crudo)
    return {"ref": ref, "dia": dia[:1].upper() + dia[1:], "lema": lema, "video": video.group(0) if video else None}


def evangelio(d):
    ymd = f"{d:%Y-%m-%d}"
    dmy = f"{d.day}-{d.month}-{d.year}"
    ruta = "homilia" if d.weekday() == 6 else "evangelio-del-dia"
    dominicos_url = f"https://www.dominicos.org/predicacion/{ruta}/{dmy}/"
    enlaces = [
        ("Dominicos", dominicos_url),
        ("Corazón de Paúl", f"https://www.corazondepaul.org/lectio/{ymd}/"),
        ("ACI Prensa", f"https://www.aciprensa.com/calendario/{ymd}"),
        ("EWTN", f"https://www.ewtn.com/es/catolicismo/lecturas/{ymd}"),
    ]
    res = {"url": enlaces[-1][1], "ref": None, "dia": None, "lema": None, "video": None, "enlaces": enlaces}
    try:
        crudo = fetch("https://www.dominicos.org/predicacion/evangelio-del-dia/hoy/").decode("utf-8", "ignore")
        dom = _dominicos(d, crudo)
        print(f"  Evangelio (Dominicos): {(dom or {}).get('ref') or 'sin datos'}")
        if dom:
            res.update({k: v for k, v in dom.items() if v})
    except Exception as e:
        print(f"  ! Evangelio (Dominicos): {e}", file=sys.stderr)
    for nombre, url in enlaces[1:]:
        if res["ref"]:
            break
        try:
            res["ref"] = ref_evangelio(limpiar(fetch(url).decode("utf-8", "ignore")))
            print(f"  Evangelio ({nombre}): {res['ref'] or 'sin cita reconocida'}")
        except Exception as e:
            print(f"  ! Evangelio ({nombre}): {e}", file=sys.stderr)
    return res


# ---------------------------------------------------------------------------
# Texto del pasaje (Reina-Valera 1909, dominio público) vía bible.helloao.org
# Se descarga una sola vez y se guarda en el repositorio (evangelios_rv1909.json).
# ---------------------------------------------------------------------------
BIBLIA_URL = "https://bible.helloao.org/api/spa_r09/complete.json"
BIBLIA_CACHE = ROOT / "evangelios_rv1909.json"
EVANGELIOS_ID = {"Mateo": "MAT", "Marcos": "MRK", "Lucas": "LUK", "Juan": "JHN"}


def _texto_nodo(x):
    if isinstance(x, str):
        return x
    if isinstance(x, list):
        return "".join(_texto_nodo(i) for i in x)
    if isinstance(x, dict):
        if "text" in x:
            return str(x["text"])
        if x.get("type") in ("footnote", "note") or "noteId" in x:
            return ""
        if "content" in x:
            return _texto_nodo(x["content"])
    return ""


def _extraer_evangelios(data):
    libros = data.get("books") if isinstance(data, dict) else data
    res = {}
    for b in libros or []:
        bid = str(b.get("id", "")).upper()
        nombre = next((n for n, i in EVANGELIOS_ID.items() if i == bid), None)
        if not nombre:
            continue
        caps = {}
        for c in b.get("chapters", []):
            ch = c.get("chapter", c)
            versos = {}
            for item in ch.get("content", []):
                if isinstance(item, dict) and item.get("type") == "verse" and item.get("number") is not None:
                    t = limpiar(_texto_nodo(item.get("content")))
                    if t:
                        k = str(item["number"])
                        versos[k] = (versos.get(k, "") + " " + t).strip()
            if ch.get("number") is not None and versos:
                caps[str(ch["number"])] = versos
        res[nombre] = caps
    return res


def evangelios_rv():
    if BIBLIA_CACHE.exists():
        return json.loads(BIBLIA_CACHE.read_text(encoding="utf-8"))
    data = json.loads(fetch(BIBLIA_URL, timeout=180).decode("utf-8"))
    ev = _extraer_evangelios(data)
    if not any(ev.values()):
        top = list(data.keys()) if isinstance(data, dict) else type(data).__name__
        libro = (data.get("books") or [{}])[0] if isinstance(data, dict) else {}
        print(f"  ! Texto bíblico: estructura no reconocida. Claves: {top}; "
              f"libro[0]: {list(libro.keys()) if isinstance(libro, dict) else '?'}", file=sys.stderr)
        return None
    BIBLIA_CACHE.write_text(json.dumps(ev, ensure_ascii=False), encoding="utf-8")
    print("  Texto bíblico: evangelios guardados en caché")
    return ev


def pasaje_rv(ref):
    """Versículos de una cita simple como 'Mateo 22, 1-14'. Devuelve [(n, texto), ...] o None."""
    m = re.fullmatch(r"(Mateo|Marcos|Lucas|Juan) (\d+), (\d+)[a-d]?(?:-(\d+)[a-d]?)?", ref or "")
    if not m:
        return None
    ev = evangelios_rv()
    if not ev:
        return None
    libro, cap, a = m.group(1), m.group(2), int(m.group(3))
    b = int(m.group(4) or a)
    versos = (ev.get(libro) or {}).get(cap) or {}
    out = [(n, versos[str(n)]) for n in range(a, b + 1) if str(n) in versos]
    return out or None


def santoral(d):
    datos = json.loads((ROOT / "santoral.json").read_text(encoding="utf-8"))
    return datos.get(f"{d.month:02d}-{d.day:02d}", [])


def mensaje(d):
    msgs = json.loads((ROOT / "mensajes.json").read_text(encoding="utf-8"))
    return msgs[d.timetuple().tm_yday % len(msgs)]


# ---------------------------------------------------------------------------
# Imagen de vista previa (1200x630)
# ---------------------------------------------------------------------------
def _fuente(tam):
    for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
              "/usr/share/fonts/dejavu/DejaVuSerif-Bold.ttf",
              "DejaVuSerif-Bold.ttf"):
        try:
            return ImageFont.truetype(p, tam)
        except Exception:
            continue
    return ImageFont.load_default()


def dos_lineas(t):
    if len(t) <= 34:
        return [t]
    mitad = len(t) // 2
    mejor = min((i for i, c in enumerate(t) if c == " "), key=lambda i: abs(i - mitad))
    return [t[:mejor], t[mejor + 1:]]


def hexc(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def imagen_og(ruta, texto, subtitulo):
    K = 2
    W, H = 1200 * K, 630 * K
    f = 1.5 * K
    stops = [(0, hexc("2f6f8f")), (.55, hexc("f2b66d")), (1, hexc("f8dd9b"))]
    img = Image.new("RGB", (W, H))
    px = ImageDraw.Draw(img)
    for y in range(H):
        t = y / (H - 1)
        for i in range(len(stops) - 1):
            if stops[i][0] <= t <= stops[i + 1][0]:
                u = (t - stops[i][0]) / (stops[i + 1][0] - stops[i][0])
                c = tuple(int(stops[i][1][k] + (stops[i + 1][1][k] - stops[i][1][k]) * u) for k in range(3))
                break
        px.line([(0, y), (W, y)], fill=c)
    img = img.convert("RGBA")
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    hd = ImageDraw.Draw(halo)
    cx, cy = 540 * f, 250 * f
    for r in range(int(150 * f), 0, -4):
        hd.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(255, 243, 196, int(240 * (1 - r / (150 * f)) ** 1.6)))
    img = Image.alpha_composite(img, halo)
    d = ImageDraw.Draw(img, "RGBA")
    d.ellipse([540 * f - 48 * f, 255 * f - 48 * f, 540 * f + 48 * f, 255 * f + 48 * f], fill=(255, 240, 184, 255))

    def P(pts):
        return [(x * f, y * f) for x, y in pts]

    def curva(p0, segs):
        pts, cur = [p0], p0
        for c, e in segs:
            for i in range(1, 31):
                t = i / 30
                pts.append(((1 - t) ** 2 * cur[0] + 2 * (1 - t) * t * c[0] + t * t * e[0],
                            (1 - t) ** 2 * cur[1] + 2 * (1 - t) * t * c[1] + t * t * e[1]))
            cur = e
        return pts

    d.polygon(P([(0, 300), (120, 235), (210, 285), (330, 205), (470, 290), (600, 240), (720, 285), (800, 250), (800, 420), (0, 420)]), fill=(76, 140, 122, 190))
    d.polygon(P([(90, 420), (300, 215), (330, 200), (360, 215), (560, 420)]), fill=hexc("2d6a58") + (255,))
    d.polygon(P([(300, 215), (330, 200), (360, 215), (345, 224), (330, 218), (315, 226)]), fill=hexc("6b8f82") + (255,))
    d.polygon(P(curva((0, 340), [((150, 300), (300, 345)), ((460, 390), (620, 335)), ((710, 307), (800, 340))]) + [(800, 420), (0, 420)]), fill=hexc("1d4f40") + (255,))
    d.polygon(P(curva((0, 385), [((200, 355), (400, 385)), ((600, 415), (800, 380))]) + [(800, 420), (0, 420)]), fill=hexc("143a30") + (255,))
    ave = curva((170, 140), [((184, 124), (198, 140))]) + curva((198, 140), [((212, 124), (226, 140))])[1:]
    d.line(P(ave), fill=hexc("14323c") + (255,), width=int(4 * f), joint="curve")

    banda = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    bd = ImageDraw.Draw(banda)
    for yy in range(int(H * .42), H):
        bd.line([(0, yy), (W, yy)], fill=(8, 28, 24, int(185 * max(0.0, (yy - H * .42) / (H * .58)) ** 1.3)))
    img = Image.alpha_composite(img, banda).convert("RGB")
    d = ImageDraw.Draw(img)
    grande, chica = _fuente(44 * K), _fuente(26 * K)

    def centrado(t, fnt, y, color):
        d.text(((W - d.textlength(t, font=fnt)) / 2, y), t, font=fnt, fill=color)

    y0 = int(H * .56)
    for n, linea in enumerate(dos_lineas(texto)):
        centrado(linea, grande, y0 + n * 66 * K, (255, 255, 255))
    centrado(subtitulo, chica, y0 + 150 * K, (255, 236, 190))
    img.resize((1200, 630), Image.LANCZOS).save(ruta, quality=90, optimize=True)


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------
CSS = """
:root{box-sizing:border-box;padding-top:env(safe-area-inset-top,0px);padding-bottom:env(safe-area-inset-bottom,0px);
--ink:#16252d;--muted:#4f6169;--paper:#f4f7f5;--card:#fff;--jade:#1d6b59;--sun:#d98a1f;--mist:#dfe9e5;--line:#cfdcd6;
--serif:"Newsreader",Georgia,"Times New Roman",serif;--sans:"Instrument Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--ink:#e6efeb;--muted:#9fb3ab;--paper:#0f1a1f;--card:#16252c;--jade:#5fc4a6;--sun:#f0b24d;--mist:#1c2f36;--line:#2a3f47}}
:root[data-theme="dark"]{--ink:#e6efeb;--muted:#9fb3ab;--paper:#0f1a1f;--card:#16252c;--jade:#5fc4a6;--sun:#f0b24d;--mist:#1c2f36;--line:#2a3f47}
html{scroll-padding-top:calc(env(safe-area-inset-top,0px) + 56px);scroll-behavior:smooth}
*,*::before,*::after{box-sizing:inherit}
body{margin:0;background:var(--paper);color:var(--ink);font-family:var(--sans);font-size:17px;line-height:1.6;-webkit-text-size-adjust:100%}
a{color:var(--jade);text-underline-offset:3px}
a:focus-visible,button:focus-visible{outline:3px solid var(--sun);outline-offset:2px;border-radius:4px}
.wrap{max-width:680px;margin:0 auto;padding:0 20px}
header.top{padding:28px 0 8px}
.brand{font-family:var(--serif);font-weight:700;font-size:1.05rem;color:var(--jade);margin:0}
.date{color:var(--muted);margin:2px 0 0;font-size:.95rem}
h1{font-family:var(--serif);font-weight:700;font-size:clamp(2.1rem,8vw,3.1rem);line-height:1.08;margin:18px 0 10px;letter-spacing:-.01em}
.lede{font-size:1.1rem;color:var(--muted);margin:0 0 6px}
.meta{font-size:.9rem;color:var(--muted);margin:10px 0 0}
.essential{margin:26px 0 8px;padding:18px 20px;background:var(--mist);border-radius:14px}
.essential h2{margin:0 0 8px;font-size:1.15rem}
.essential ol{margin:0;padding-left:1.2rem}
.essential li{margin:6px 0}
nav.chips{position:sticky;top:env(safe-area-inset-top,0px);z-index:5;background:var(--paper);border-bottom:1px solid var(--line);margin:18px -20px 0;padding:10px 20px;display:flex;gap:8px;overflow-x:auto;-webkit-overflow-scrolling:touch}
nav.chips a{flex:0 0 auto;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);padding:6px 14px;border-radius:999px;font-size:.92rem;font-weight:500}
section{padding:34px 0 6px;border-bottom:1px solid var(--line)}
section:last-of-type{border-bottom:0}
h2.sec{font-family:var(--serif);font-size:1.7rem;line-height:1.15;margin:0 0 4px}
.item{margin:22px 0 0}
.tag{display:inline-block;font-size:.85rem;font-weight:600;color:var(--sun);margin:0 0 2px}
h3{font-family:var(--serif);font-size:1.25rem;line-height:1.25;margin:0 0 6px}
.item p{margin:0 0 8px}
.src{font-size:.88rem;color:var(--muted)}
.vacio{color:var(--muted)}
.saints{list-style:none;padding:0;margin:14px 0 0}
.saints li{padding:12px 0;border-top:1px solid var(--line)}
.saints strong{display:block;font-family:var(--serif);font-size:1.15rem}
.saints span{color:var(--muted);font-size:.95rem}
figure.mensaje{margin:16px 0 0}
figure.mensaje svg{display:block;width:100%;height:auto;border-radius:16px}
blockquote{font-family:var(--serif);font-size:1.45rem;line-height:1.3;margin:18px 0 6px;padding:0;font-weight:500}
figcaption{font-size:.85rem;color:var(--muted)}
.share{margin:18px 0 0;display:flex;align-items:center;gap:12px;flex-wrap:wrap}
button.btn{font:inherit;font-weight:600;background:var(--jade);color:#fff;border:0;padding:11px 20px;border-radius:999px;cursor:pointer;min-height:44px}
:root[data-theme="dark"] button.btn{color:#0f1a1f}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]) button.btn{color:#0f1a1f}}
#copiado{color:var(--muted);font-size:.9rem}
.edicion{display:block;text-decoration:none;color:var(--ink);background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px 18px;margin:12px 0}
.edicion strong{font-family:var(--serif);font-size:1.2rem;display:block}
.edicion span{color:var(--muted);font-size:.92rem}
.edicion .nuevo{color:var(--sun);font-weight:600}
.sant-head{display:flex;align-items:center;gap:14px}.sant-head h2{margin:0}.sant-icon{flex:0 0 auto}
.region{border-top:1px solid var(--line);padding:10px 0}.region summary{cursor:pointer;list-style:none}.region summary::-webkit-details-marker{display:none}
.region summary strong{display:block;font-family:var(--serif);font-size:1.15rem}.region summary span{color:var(--muted);font-size:.95rem}
.region dl{margin:10px 0 4px}.region dt{font-weight:600;margin-top:6px}.region dd{margin:0}
.aviso{margin:18px 0 0;padding:14px 16px;border-left:4px solid var(--sun);background:var(--mist);border-radius:0 12px 12px 0}
.aviso h3{margin:2px 0 6px}.aviso p{margin:0 0 6px}
.lectura{margin:10px 0 0;border:1px solid var(--line);border-radius:12px;padding:10px 14px;background:var(--card)}.lectura summary{cursor:pointer;font-weight:600;color:var(--jade)}.lectura p{margin:10px 0 0}.lectura sup{color:var(--sun);font-weight:600;margin-right:4px}
footer{padding:30px 0 40px;color:var(--muted);font-size:.88rem}
@media (prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""

SVG = """<svg viewBox="0 0 800 420" role="img" aria-label="Amanecer sobre montañas verdes con un volcán y un ave volando">
<defs><linearGradient id="cielo" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#2f6f8f"/><stop offset=".55" stop-color="#f2b66d"/><stop offset="1" stop-color="#f8dd9b"/></linearGradient>
<radialGradient id="halo" cx="50%" cy="50%" r="50%"><stop offset="0" stop-color="#fff3c4" stop-opacity=".95"/><stop offset="1" stop-color="#fff3c4" stop-opacity="0"/></radialGradient></defs>
<rect width="800" height="420" fill="url(#cielo)"/><circle cx="540" cy="250" r="150" fill="url(#halo)"/><circle cx="540" cy="255" r="48" fill="#fff0b8"/>
<path d="M0 300 L120 235 L210 285 L330 205 L470 290 L600 240 L720 285 L800 250 L800 420 L0 420Z" fill="#4c8c7a" opacity=".75"/>
<path d="M90 420 L300 215 L330 200 L360 215 L560 420Z" fill="#2d6a58"/><path d="M300 215 L330 200 L360 215 L345 224 L330 218 L315 226Z" fill="#6b8f82"/>
<path d="M0 340 Q150 300 300 345 T620 335 T800 340 L800 420 L0 420Z" fill="#1d4f40"/><path d="M0 385 Q200 355 400 385 T800 380 L800 420 L0 420Z" fill="#143a30"/>
<path d="M170 140 q14 -16 28 0 q14 -16 28 0" fill="none" stroke="#14323c" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/></svg>"""

ICONO_SANTORAL = '<svg class="sant-icon" width="64" height="64" viewBox="0 0 120 120" aria-hidden="true" focusable="false"><circle cx="60" cy="60" r="56" fill="#1d4f40"/><circle cx="60" cy="60" r="44" fill="none" stroke="#f0b24d" stroke-width="3"/><g stroke="#f0b24d" stroke-width="3" stroke-linecap="round"><line x1="60" y1="7" x2="60" y2="15" transform="rotate(0 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(45 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(90 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(135 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(180 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(225 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(270 60 60)"/><line x1="60" y1="7" x2="60" y2="15" transform="rotate(315 60 60)"/></g><rect x="55" y="30" width="10" height="60" rx="3" fill="#fff0b8"/><rect x="38" y="47" width="44" height="10" rx="3" fill="#fff0b8"/></svg>'

FUENTES_WEB = ('<link rel="preconnect" href="https://fonts.googleapis.com">'
               '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
               '<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,700'
               '&family=Instrument+Sans:wght@400;500;600&display=swap" rel="stylesheet">')

JS_COMPARTIR = """<script>(function(){var b=document.getElementById('compartir'),s=document.getElementById('copiado');if(!b)return;
b.addEventListener('click',function(){var d={title:document.title,url:location.href};try{if(navigator.share){navigator.share(d).catch(function(){});return;}
if(navigator.clipboard){navigator.clipboard.writeText(location.href).then(function(){s.textContent='Enlace copiado';});return;}}catch(e){}
s.textContent='Copie el enlace desde la barra del navegador';});})();</script>"""


def cabecera(titulo, desc, url, img, tipo):
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{esc(titulo)}</title>
<meta name="description" content="{esc(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="{tipo}">
<meta property="og:site_name" content="{NOMBRE}">
<meta property="og:locale" content="es_CR">
<meta property="og:url" content="{url}">
<meta property="og:title" content="{esc(titulo)}">
<meta property="og:description" content="{esc(desc)}">
<meta property="og:image" content="{img}">
<meta property="og:image:type" content="image/jpeg">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="Amanecer sobre montañas con un mensaje positivo.">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{esc(titulo)}">
<meta name="twitter:description" content="{esc(desc)}">
<meta name="twitter:image" content="{img}">
{FUENTES_WEB}
<style>{CSS}</style>
</head>
"""


@lru_cache(maxsize=250)
def detectar_posible_paywall(url):
    """Devuelve True solo si el HTML deja señales claras de muro de pago.

    Es una comprobación orientativa: los muros cargados con JavaScript o los
    bloqueos anti-bots pueden no detectarse. No se usa para descartar noticias.
    """
    patrones = (
        "solo para suscriptores", "solo suscriptores", "contenido exclusivo",
        "suscríbete para continuar", "suscribete para continuar",
        "subscribe to continue", "subscriber-only", "paywall",
        "inicia sesión para continuar", "inicia sesion para continuar",
    )
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
        with urllib.request.urlopen(req, timeout=3) as r:
            tipo = r.headers.get("Content-Type", "").lower()
            if "html" not in tipo:
                return False
            contenido = r.read(80000).decode("utf-8", errors="ignore").lower()
        return any(p in contenido for p in patrones)
    except Exception:
        return False


def item_html(it):
    # Comprobación orientativa en todas las fuentes. Timeout corto para que
    # una página lenta no detenga la publicación; no se eliminan titulares.
    aviso_pago = ' <span class="tag">Posible muro de pago</span>' if detectar_posible_paywall(it["link"]) else ""
    s = f'<div class="item"><span class="tag">{esc(it["fuente"])}</span>{aviso_pago}<h3><a href="{esc(it["link"])}" style="color:inherit">{esc(it["titulo"])}</a></h3>'
    if it["resumen"]:
        s += f'<p>{esc(it["resumen"])}</p>'
    s += f'<p class="src">Nota completa: <a href="{esc(it["link"])}">{esc(it["fuente"])}</a></p></div>'
    return s


def _c(x):
    return f"{x:.1f}".replace(".", ",")


def tiempo_html(t):
    if not t:
        return ""
    h = '<section id="tiempo"><h2 class="sec">El tiempo</h2>\n'
    a = t.get("aviso")
    if a and a.get("reciente"):
        h += '<div class="aviso"><span class="tag">Aviso meteorológico del IMN</span>'
        h += f'<h3><a href="{esc(a["link"])}" style="color:inherit">{esc(a["titulo"])}</a></h3>'
        if a.get("fecha"):
            h += f'<p class="src">{esc(a["fecha"])}</p>'
        if a.get("diag"):
            h += f'<p>{esc(a["diag"])}</p>'
        h += '</div>\n'
    elif a:
        h += f'<p class="vacio">Sin avisos recientes del IMN. <a href="{imn.URL_AVISOS}">Ver el historial de avisos</a>.</p>\n'
    p = t.get("pronostico")
    if p:
        h += f'<div class="item"><span class="tag">Pronóstico regional</span>'
        if p.get("valido"):
            h += f'<p class="src">Válido para: {esc(p["valido"])}. Toque una región para ver el detalle.</p>'
        h += '</div>\n'
        for r in p["regiones"]:
            per = r["periodos"]
            resumen = f'Tarde: {per[2]}' if len(per) > 2 and per[2] else ""
            h += f'<details class="region"><summary><strong>{esc(r["nombre"])}</strong><span>{esc(resumen)}</span></summary><dl>'
            for nombre, txt in zip(imn.PERIODOS, per):
                if txt:
                    h += f'<dt>{nombre}</dt><dd>{esc(txt)}</dd>'
            h += '</dl>'
            if r["ciudades"]:
                h += '<p class="src">' + " · ".join(f'{esc(c)} {_c(mn)}–{_c(mx)} °C' for c, mx, mn in r["ciudades"]) + '</p>'
            h += '</details>\n'
        if p.get("comentario"):
            h += f'<div class="item"><h3>Comentario general</h3><p>{esc(recortar(p["comentario"], 420))}</p></div>\n'
        extra = []
        if p.get("sol"):
            extra.append(f'Sol: sale {p["sol"][0]}, se pone {p["sol"][1]}')
        if p.get("luna"):
            extra.append(f'Luna: sale {p["luna"][0]}, se pone {p["luna"][1]}')
        if p.get("fase"):
            extra.append(f'Fase lunar: {p["fase"].lower()}')
        if extra:
            h += f'<p class="src">San José · {esc(" · ".join(extra))}</p>\n'
    h += f'<p class="src">Fuente: <a href="{imn.URL_PRONOSTICO}">Instituto Meteorológico Nacional</a> · <a href="{imn.URL_AVISOS}">Avisos</a></p></section>\n'
    return h


def imagen_mensaje_diaria(d):
    """Fotografía gratuita aleatoria, estable durante el día y distinta al cambiar la fecha."""
    clave = f"positivo-{d:%Y%m%d}"
    return f"https://picsum.photos/seed/{clave}/1200/630"


def pagina_edicion(d, secciones, eva, santos, msg, clima=None):
    ymd = f"{d:%Y-%m-%d}"
    larga = fecha_larga(d)
    url = f"{BASE}/ediciones-{ymd}.html"
    titulo = f"{NOMBRE} · {larga}"
    desc = "Costa Rica, deportes, cultura, tecnología, el mundo, el Evangelio, el santoral y un mensaje positivo para comenzar el día."
    esenciales = []
    for sid in ("costa-rica", "deportes", "internacional"):
        its = secciones.get(sid) or []
        if its:
            esenciales.append((sid, its[0]["titulo"]))
    img_diaria = imagen_mensaje_diaria(d)
    h = cabecera(titulo, desc, url, img_diaria, "article")
    h += f'<body>\n<div class="wrap">\n<header class="top">\n<p class="brand"><a href="index.html" style="text-decoration:none">{NOMBRE}</a> · <a href="index.html">Archivo de ediciones</a></p>\n'
    h += f'<p class="date">{larga}</p>\n<h1>Noticias de hoy, en cinco minutos</h1>\n'
    h += '<p class="lede">Costa Rica, deportes, tecnología, el mundo, el santoral y una dosis de motivación para comenzar el día.</p>\n'
    h += '<p class="meta">Edición diaria · Titulares tomados de los medios citados</p>\n</header>\n'
    if esenciales:
        h += '<div class="essential"><h2>Lo esencial de hoy</h2><ol>'
        for sid, t in esenciales:
            h += f'<li><a href="#{sid}">{esc(t)}</a></li>'
        h += '</ol></div>\n'
    h += '<nav class="chips" aria-label="Secciones">'
    for s in SECCIONES:
        h += f'<a href="#{s["id"]}">{esc(s["titulo"])}</a>'
        if s["id"] == "costa-rica" and clima:
            h += '<a href="#tiempo">El tiempo</a>'
    h += '<a href="#evangelio">Evangelio</a><a href="#santoral">Santoral</a><a href="#mensaje">Mensaje positivo</a></nav>\n'
    for s in SECCIONES:
        h += f'<section id="{s["id"]}"><h2 class="sec">{esc(s["titulo"])}</h2>\n'
        its = secciones.get(s["id"]) or []
        h += "\n".join(item_html(i) for i in its) if its else '<p class="vacio">Hoy no hay titulares disponibles en esta sección.</p>'
        h += "</section>\n"
        if s["id"] == "costa-rica":
            h += tiempo_html(clima)
    ref = eva["ref"] or "El Evangelio de hoy"
    enlaces = " · ".join(f'<a href="{esc(u)}">{esc(n)}</a>' for n, u in eva.get("enlaces", [("EWTN", eva["url"])]))
    h += '<section id="evangelio"><h2 class="sec">Evangelio del día</h2><div class="item">'
    if eva.get("dia"):
        h += f'<span class="tag">{esc(eva["dia"])}</span>'
    h += f'<h3>{esc(ref)}</h3>'
    if eva.get("lema"):
        h += f'<p>Tema de la homilía: «{esc(eva["lema"])}»</p>'
    h += f'<p class="src">Lea el Evangelio completo: {enlaces}</p>'
    if eva.get("video"):
        h += f'<p class="src"><a href="{esc(eva["video"])}">Ver el Evangelio de hoy en vídeo</a></p>'
    if eva.get("texto"):
        h += '<details class="lectura"><summary>Leer el pasaje aquí</summary>'
        h += "".join(f'<p><sup>{n}</sup> {esc(t)}</p>' for n, t in eva["texto"])
        h += '<p class="src">Texto: Reina-Valera 1909 (dominio público). La traducción que se proclama en misa es otra, así que puede haber diferencias.</p></details>'
    h += '</div></section>\n'
    h += f'<section id="santoral"><div class="sant-head">{ICONO_SANTORAL}<h2 class="sec">Santoral del {d.day} de {MESES[d.month - 1]}</h2></div>'
    if santos:
        h += '<ul class="saints">' + "".join(f'<li><strong>{esc(n)}</strong><span>{esc(r)}</span></li>' for n, r in santos) + '</ul>'
    else:
        h += '<p class="vacio">Consulte el santoral completo del día en el enlace de abajo.</p>'
    h += f'<p class="src" style="margin-top:12px"><a href="https://www.aciprensa.com/calendario/{ymd}">Santoral y lecturas en ACI Prensa</a></p></section>\n'
    h += f'<section id="mensaje"><h2 class="sec">Mensaje positivo</h2><figure class="mensaje"><img src="{esc(img_diaria)}" alt="Fotografía inspiradora del día" loading="lazy" style="display:block;width:100%;height:auto;border-radius:16px"><blockquote>{esc(msg)}</blockquote>'
    h += f'<figcaption>Fotografía gratuita de Lorem Picsum · {larga}</figcaption></figure>'
    h += '<div class="share"><button class="btn" id="compartir" type="button">Compartir esta edición</button><span id="copiado" role="status" aria-live="polite"></span></div></section>\n'
    h += f'<footer><a href="index.html">Volver al archivo de ediciones</a><br>{NOMBRE} · Edición del {larga.lower()}. Los titulares y extractos pertenecen a los medios citados; siga cada enlace para leer la nota completa.</footer>\n</div>\n{JS_COMPARTIR}\n</body>\n</html>\n'
    return h


def pagina_indice(eds):
    ult = eds[0]
    d = datetime.strptime(ult["fecha"], "%Y-%m-%d")
    h = cabecera(f"{NOMBRE} · Archivo de ediciones",
                 "Boletín diario de noticias de Costa Rica, deportes, tecnología, el mundo, el Evangelio, el santoral y un mensaje positivo. Archivo de todas las ediciones.",
                 f"{BASE}/", imagen_mensaje_diaria(d), "website")
    h += f'<body>\n<div class="wrap">\n<header class="top"><p class="brand">{NOMBRE}</p><p class="date">{fecha_larga(d)}</p>\n'
    h += '<h1>El boletín, edición por edición</h1>\n<p class="lede">Costa Rica, deportes, tecnología, el mundo, el Evangelio del día, el santoral y una dosis de motivación para comenzar el día.</p>\n'
    h += f'<p class="meta"><strong>Edición más reciente:</strong> {fecha_larga(d).lower()}</p>\n'
    h += f'<p style="margin:18px 0 0"><button class="btn" type="button" onclick="location.href=\'ediciones-{ult["fecha"]}.html\'">Leer la edición de hoy</button></p></header>\n'
    h += '<section id="ediciones" style="border-bottom:0"><h2 class="sec">Todas las ediciones</h2>\n'
    for n, e in enumerate(eds):
        dd = datetime.strptime(e["fecha"], "%Y-%m-%d")
        marca = '<span class="nuevo">Edición más reciente</span> · ' if n == 0 else ""
        h += f'<a class="edicion" href="ediciones-{e["fecha"]}.html"><strong>{fecha_larga(dd)}</strong><span>{marca}{esc(e["resumen"])}</span></a>\n'
    h += f'</section>\n<footer>{NOMBRE} · Archivo de ediciones.</footer>\n</div>\n</body>\n</html>\n'
    return h


def aviso(d, secciones, url, clima=None, eva=None):
    """Texto del mensaje de Telegram (formato HTML de Telegram)."""
    lin = [f"<b>{NOMBRE} 🇨🇷</b>", fecha_larga(d), ""]
    for sid in ("costa-rica", "deportes", "internacional"):
        its = secciones.get(sid) or []
        if its:
            lin.append("• " + html.escape(recortar(its[0]["titulo"], 110), quote=False))
    corto = imn.resumen_corto(clima)
    if corto:
        lin += ["", "☁️ " + html.escape(corto, quote=False)]
    av = (clima or {}).get("aviso")
    if av and av.get("reciente"):
        lin.append("⚠️ Aviso del IMN: " + html.escape(recortar(av["titulo"], 100), quote=False))
    if eva and eva.get("ref"):
        lin.append("📖 Evangelio: " + html.escape(eva["ref"], quote=False))
    lin += ["", f'<a href="{url}">Leer la edición completa</a>']
    return "\n".join(lin)


# ---------------------------------------------------------------------------
def main():
    ahora = datetime.now(timezone.utc)
    hoy = ahora.astimezone(TZ).date()
    ymd = f"{hoy:%Y-%m-%d}"
    destino = ROOT / f"ediciones-{ymd}.html"
    # Siempre regenerar la edición del día. Así, al volver a ejecutar el flujo
    # después de actualizar el código, se actualizan también la foto y la vista previa.
    # Las ediciones de fechas anteriores no se modifican.
    if destino.exists():
        print(f"La edición {ymd} ya existe; se actualizará con la versión actual del programa.")
    print(f"Armando edición {ymd}")
    vistos, secciones = [], {}
    for s in SECCIONES:
        print(f"- {s['titulo']}")
        secciones[s["id"]] = reunir(s, ahora, vistos)
    total = sum(len(v) for v in secciones.values())
    if total == 0:
        print("Ninguna fuente respondió; no se publica una edición vacía.", file=sys.stderr)
        sys.exit(1)
    eva, santos, msg = evangelio(hoy), santoral(hoy), mensaje(hoy)
    try:
        eva["texto"] = pasaje_rv(eva.get("ref"))
    except Exception as e:
        print(f"  ! Texto bíblico: {e}", file=sys.stderr)
        eva["texto"] = None
    try:
        clima = imn.obtener(fetch, parse_feed, ahora)
    except Exception as e:
        print(f"  ! Tiempo: {e}", file=sys.stderr)
        clima = None
    print("- El tiempo:", "listo" if clima else "no disponible")
    destino.write_text(pagina_edicion(hoy, secciones, eva, santos, msg, clima), encoding="utf-8")
    if os.environ.get("AVISO_PATH"):
        Path(os.environ["AVISO_PATH"]).write_text(
            aviso(hoy, secciones, f"{BASE}/ediciones-{ymd}.html", clima, eva), encoding="utf-8")
    sub = f"{NOMBRE} · {hoy.day} de {MESES[hoy.month - 1]} de {hoy.year}"
    imagen_og(ROOT / f"ediciones-{ymd}-og.jpg", msg, sub)
    imagen_og(ROOT / "og-image.jpg", msg, sub)

    ruta = ROOT / "ediciones.json"
    eds = json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else []
    eds = [e for e in eds if e["fecha"] != ymd]
    primero = (secciones.get("costa-rica") or secciones.get("internacional") or [{"titulo": "Edición diaria"}])[0]["titulo"]
    eds.append({"fecha": ymd, "resumen": recortar(primero, 110)})
    eds.sort(key=lambda e: e["fecha"], reverse=True)
    ruta.write_text(json.dumps(eds, ensure_ascii=False, indent=2), encoding="utf-8")
    (ROOT / "index.html").write_text(pagina_indice(eds), encoding="utf-8")
    print(f"Listo: {total} notas.")


if __name__ == "__main__":
    main()
