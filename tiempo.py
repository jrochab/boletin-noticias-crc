"""Pronóstico regional y avisos del IMN (Instituto Meteorológico Nacional de Costa Rica).

Todo está protegido: si el IMN cambia su página o no responde, la sección simplemente
se omite o se reduce a enlaces, y la edición se publica igual.
"""
import html
import re
import unicodedata
from datetime import timedelta
from html.parser import HTMLParser

URL_PRONOSTICO = "https://www.imn.ac.cr/es/web/imn/reporte-pronostico-regional"
URL_AVISOS = "https://www.imn.ac.cr/web/imn/avisos-meteorologicos"
RSS_AVISOS = [
    "https://www.imn.ac.cr/es/web/imn/avisos-meteorologicos/-/asset_publisher/lTlyP4wEZlDa/rss.xml",
    "https://www.imn.ac.cr/en/web/imn/avisos-meteorologicos/-/asset_publisher/lTlyP4wEZlDa/rss?p_p_cacheability=cacheLevelPage",
]
REGIONES = ["Valle Central", "Pacífico Norte", "Pacífico Central", "Pacífico Sur",
            "Caribe Norte", "Caribe Sur", "Zona Norte"]
PERIODOS = ["Madrugada", "Mañana", "Tarde", "Noche"]
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}


# ---------------------------------------------------------------- árbol HTML mínimo
class Nodo:
    __slots__ = ("tag", "attrs", "hijos", "padre")

    def __init__(self, tag, attrs=None, padre=None):
        self.tag, self.attrs, self.hijos, self.padre = tag, attrs or {}, [], padre


class Arbol(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.raiz = Nodo("root")
        self.act = self.raiz

    def _cerrar(self, tags):
        n = self.act
        while n is not None and n.tag != "table":
            if n.tag in tags:
                self.act = n.padre
                return
            n = n.padre

    def handle_starttag(self, tag, attrs):
        if tag in ("td", "th"):
            self._cerrar(("td", "th"))
        elif tag == "tr":
            self._cerrar(("tr",))
        n = Nodo(tag, dict(attrs), self.act)
        self.act.hijos.append(n)
        if tag not in VOID:
            self.act = n

    def handle_startendtag(self, tag, attrs):
        self.act.hijos.append(Nodo(tag, dict(attrs), self.act))

    def handle_endtag(self, tag):
        n = self.act
        while n is not None and n.tag != tag:
            n = n.padre
        if n is not None and n is not self.raiz:
            self.act = n.padre

    def handle_data(self, d):
        self.act.hijos.append(d)


def texto(n):
    if isinstance(n, str):
        return n
    if n.tag in ("script", "style", "noscript"):
        return ""
    return " ".join(texto(h) for h in n.hijos)


def limpio(t):
    return re.sub(r"\s+", " ", html.unescape(t or "")).strip()


def descendientes(n):
    yield n
    for h in n.hijos:
        if not isinstance(h, str):
            yield from descendientes(h)


def _norm(s):
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c)).strip()


def _num(s):
    s = s.strip().replace(",", ".")
    return float(s) if re.fullmatch(r"-?\d+(?:\.\d+)?", s) else None


def _celdas(tr):
    return [h for h in tr.hijos if not isinstance(h, str) and h.tag in ("td", "th")]


# ---------------------------------------------------------------- pronóstico regional
def pronostico_regional(raw):
    arbol = Arbol()
    arbol.feed(raw)
    plano = limpio(texto(arbol.raiz))
    esperadas = {_norm(r): r for r in REGIONES}
    regiones = []
    for n in descendientes(arbol.raiz):
        if n.tag != "tr":
            continue
        tds = _celdas(n)
        if len(tds) < 5:
            continue
        nombre = esperadas.get(_norm(limpio(texto(tds[0]))))
        if not nombre:
            continue
        periodos = [limpio(texto(c)) for c in tds[1:5]]
        ciudades = []
        if len(tds) > 5:
            for r in descendientes(tds[5]):
                if r.tag == "tr":
                    c = [limpio(texto(x)) for x in _celdas(r)]
                    if len(c) >= 3 and _num(c[1]) is not None and _num(c[2]) is not None:
                        ciudades.append((c[0], _num(c[1]), _num(c[2])))
        regiones.append({"nombre": nombre, "periodos": periodos, "ciudades": ciudades})
    if not regiones:
        return None
    valido = re.search(r"Válido para:\s*(.+?)\s+Elaboró", plano)
    comentario = re.search(r"Comentario General\s+(.+?)\s+Efemérides", plano)
    sol = luna = fase = None
    ef = re.search(r"Efemérides \(San José\)\s*(.*)", plano)
    if ef:
        horas = re.findall(r"(\d{1,2})\s*:\s*(\d{2})\s*([ap])\.?\s?m\.?", ef.group(1), re.I)
        fmt = lambda h: f"{int(h[0])}:{h[1]} {h[2].lower()}. m."
        if len(horas) >= 2:
            sol = (fmt(horas[0]), fmt(horas[1]))
        if len(horas) >= 4:
            luna = (fmt(horas[2]), fmt(horas[3]))
        f = re.search(r"Fase lunar\s+(.+?)(?:\s+Contáctenos|\s+Benemérito|$)", ef.group(1))
        if f:
            fase = limpio(f.group(1))[:60]
    return {"valido": limpio(valido.group(1)) if valido else None,
            "comentario": limpio(comentario.group(1)) if comentario else None,
            "regiones": regiones, "sol": sol, "luna": luna, "fase": fase}


# ---------------------------------------------------------------- avisos meteorológicos
def _aviso_desde_rss(fetch, parse_feed, ahora):
    for url in RSS_AVISOS:
        try:
            items = parse_feed(fetch(url), "IMN")
        except Exception:
            continue
        if items:
            epoca = ahora - timedelta(days=3650)
            it = sorted(items, key=lambda i: i["fecha"] or epoca, reverse=True)[0]
            reciente = (it["fecha"] is None) or (it["fecha"] >= ahora - timedelta(hours=72))
            return {"titulo": it["titulo"], "diag": it["resumen"], "link": it["link"],
                    "fecha": None, "fecha_dt": it["fecha"], "reciente": reciente}
    return None


def _aviso_desde_pagina(fetch):
    arbol = Arbol()
    arbol.feed(fetch(URL_AVISOS).decode("utf-8", "ignore"))
    plano = limpio(texto(arbol.raiz))
    m = re.search(r"Aviso Meteorológico[^#]*?\s+(?P<t>.+?)\s+(?P<f>\d{1,2} de \w+ (?:de|del) 20\d\d|\d{2}/\d{2}/20\d\d)"
                  r"\s+(?P<h>\d{1,2}[.:]\d{2}\s*[ap]\.?\s?m\.?)\s+Diagnóstico:\s*(?P<d>.+?)\s+Ver Más", plano)
    if not m:
        return None
    link = None
    for n in descendientes(arbol.raiz):
        if n.tag == "a" and limpio(texto(n)).lower().startswith("ver m"):
            link = n.attrs.get("href")
            break
    return {"titulo": limpio(m.group("t")), "diag": limpio(m.group("d"))[:230],
            "link": link or URL_AVISOS, "fecha": f'{limpio(m.group("f"))}, {limpio(m.group("h"))}',
            "fecha_dt": None, "reciente": True}


def ultimo_aviso(fetch, parse_feed, ahora):
    try:
        a = _aviso_desde_rss(fetch, parse_feed, ahora)
        if a:
            return a
    except Exception:
        pass
    try:
        return _aviso_desde_pagina(fetch)
    except Exception:
        return None


# ---------------------------------------------------------------- punto de entrada
def obtener(fetch, parse_feed, ahora):
    pron = aviso = None
    try:
        pron = pronostico_regional(fetch(URL_PRONOSTICO).decode("utf-8", "ignore"))
    except Exception as e:
        print(f"  ! Pronóstico IMN: {e}")
    aviso = ultimo_aviso(fetch, parse_feed, ahora)
    if not pron and not aviso:
        return None
    return {"pronostico": pron, "aviso": aviso}


def resumen_corto(t):
    """Una línea para el aviso de Telegram."""
    if not t or not t.get("pronostico"):
        return None
    r = next((x for x in t["pronostico"]["regiones"] if x["nombre"] == "Valle Central"), None)
    if not r:
        return None
    tarde = r["periodos"][2] if len(r["periodos"]) > 2 else ""
    sj = next((c for c in r["ciudades"] if c[0].lower() == "san josé"), None)
    s = f"Valle Central, tarde: {tarde}" if tarde else "Valle Central"
    if sj:
        s += f" · San José {str(sj[2]).replace('.', ',')}–{str(sj[1]).replace('.', ',')} °C"
    return s
