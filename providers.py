"""Product sources: live Google Shopping (SerpAPI) and the bundled demo catalog."""

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

_CATALOG = json.loads((Path(__file__).parent / "data" / "catalog.json").read_text("utf-8"))
CATEGORIES = _CATALOG["categories"]
_CAT_IDS = {c["id"] for c in CATEGORIES}
DEMO = {p["id"]: dict(p, price_source="demo (sample price, not live)") for p in _CATALOG["products"]}

TIMEOUT = 20


_CTX = [None]


def bind(ctx):
    _CTX[0] = ctx


def setting(key: str, default=None):
    """Non-secret settings live in config.yaml under plugins.entries.shopmate.settings.<key>."""
    ctx = _CTX[0]
    if ctx is None:
        return default
    try:
        v = ctx.get_config(key, default)
    except Exception:
        return default
    return default if v in (None, "") else v


def get_secret(name: str):
    try:
        from agent.secret_scope import get_secret as _gs  # Hermes credential route
    except Exception:
        return None
    try:
        v = _gs(name)
    except Exception:
        return None
    return v or None


def _http_json(url: str, data: bytes = None, headers: dict = None, method: str = None) -> dict:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def search_demo(query: str, category: str = None, max_price: float = None, limit: int = 10):
    words = [w for w in (query or "").lower().split() if len(w) > 1]
    scored = []
    for p in DEMO.values():
        if category and p["category"] != category:
            continue
        if max_price and p["price"] > max_price:
            continue
        hay = (p["title"] + " " + p["category"] + " " + category_name(p["category"])).lower()
        score = sum(1 for w in words if w in hay or w.rstrip("s") in hay)
        if words and score < max(1, (len(words) + 1) // 2):
            continue
        scored.append((-score, p["price"], p))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [dict(t[2]) for t in scored[:limit]]


def currency_for(country: str) -> str:
    return COUNTRY_CURRENCY.get((country or "").lower(), "USD")


def search_live(api_key: str, query: str, country: str = "ng", max_price: float = None, limit: int = 10):
    params = {
        "engine": "google_shopping",
        "q": query,
        "gl": country,
        "hl": "en",
        "api_key": api_key,
    }
    data = _http_json("https://serpapi.com/search.json?" + urllib.parse.urlencode(params))
    if data.get("error"):
        raise RuntimeError(data["error"])
    out = []
    for r in data.get("shopping_results", []) or []:
        price = r.get("extracted_price")
        if price is None:
            continue
        if max_price and price > max_price:
            continue
        link = r.get("product_link") or r.get("link") or ""
        key = r.get("product_id") or (r.get("title", "") + link)
        pid = "g-" + hashlib.sha1(str(key).encode()).hexdigest()[:12]
        out.append({
            "id": pid,
            "title": r.get("title"),
            "price": price,
            "price_display": r.get("price"),
            "currency": currency_for(country),
            "image": r.get("thumbnail") or r.get("serpapi_thumbnail"),
            "merchant": r.get("source"),
            "link": link,
            "rating": r.get("rating"),
            "reviews": r.get("reviews"),
            "price_source": "live (Google Shopping)",
        })
        if len(out) >= limit:
            break
    return out


_FX = {}


def fx_rate(src: str, dst: str) -> float:
    """Live mid-market rate from open.er-api.com (free, no key), cached for an hour."""
    src, dst = src.upper(), dst.upper()
    if src == dst:
        return 1.0
    now = time.time()
    hit = _FX.get(src)
    if not hit or now - hit[0] > 3600:
        data = _http_json("https://open.er-api.com/v6/latest/" + src)
        if data.get("result") != "success":
            raise RuntimeError("FX lookup failed for " + src)
        hit = _FX[src] = (now, data["rates"])
    if dst not in hit[1]:
        raise RuntimeError("No exchange rate for %s→%s" % (src, dst))
    return float(hit[1][dst])


COUNTRY_CURRENCY = {
    # Africa
    "ng": "NGN", "gh": "GHS", "ke": "KES", "za": "ZAR", "eg": "EGP", "rw": "RWF", "ug": "UGX", "tz": "TZS",
    "et": "ETB", "ma": "MAD", "dz": "DZD", "tn": "TND", "zm": "ZMW", "zw": "USD", "mw": "MWK", "mz": "MZN",
    "ao": "AOA", "na": "NAD", "bw": "BWP", "cm": "XAF", "ga": "XAF", "cg": "XAF", "td": "XAF", "cf": "XAF",
    "gq": "XAF", "ci": "XOF", "sn": "XOF", "bj": "XOF", "tg": "XOF", "bf": "XOF", "ml": "XOF", "ne": "XOF",
    "gw": "XOF", "sl": "SLE", "lr": "LRD", "gm": "GMD", "gn": "GNF", "cd": "CDF", "sd": "SDG", "so": "SOS",
    "mu": "MUR", "mg": "MGA", "ly": "LYD",
    # Americas
    "us": "USD", "ca": "CAD", "mx": "MXN", "br": "BRL", "ar": "ARS", "cl": "CLP", "co": "COP", "pe": "PEN",
    "ve": "VES", "ec": "USD", "uy": "UYU", "py": "PYG", "bo": "BOB", "jm": "JMD", "tt": "TTD", "do": "DOP",
    "cr": "CRC", "pa": "USD", "gt": "GTQ", "hn": "HNL", "sv": "USD", "ni": "NIO", "cu": "CUP", "ht": "HTG",
    "bs": "BSD", "bb": "BBD",
    # Europe
    "gb": "GBP", "ie": "EUR", "fr": "EUR", "de": "EUR", "it": "EUR", "es": "EUR", "pt": "EUR", "nl": "EUR",
    "be": "EUR", "lu": "EUR", "at": "EUR", "fi": "EUR", "gr": "EUR", "sk": "EUR", "si": "EUR", "ee": "EUR",
    "lv": "EUR", "lt": "EUR", "mt": "EUR", "cy": "EUR", "hr": "EUR", "ch": "CHF", "no": "NOK", "se": "SEK",
    "dk": "DKK", "is": "ISK", "pl": "PLN", "cz": "CZK", "hu": "HUF", "ro": "RON", "bg": "BGN", "rs": "RSD",
    "ua": "UAH", "ru": "RUB", "by": "BYN", "md": "MDL", "al": "ALL", "mk": "MKD", "ba": "BAM", "tr": "TRY",
    "ge": "GEL", "am": "AMD", "az": "AZN",
    # Middle East & Asia
    "ae": "AED", "sa": "SAR", "qa": "QAR", "kw": "KWD", "bh": "BHD", "om": "OMR", "jo": "JOD", "lb": "LBP",
    "il": "ILS", "iq": "IQD", "ir": "IRR", "in": "INR", "pk": "PKR", "bd": "BDT", "lk": "LKR", "np": "NPR",
    "cn": "CNY", "hk": "HKD", "tw": "TWD", "jp": "JPY", "kr": "KRW", "sg": "SGD", "my": "MYR", "id": "IDR",
    "th": "THB", "vn": "VND", "ph": "PHP", "kh": "KHR", "mm": "MMK", "kz": "KZT", "uz": "UZS", "mn": "MNT",
    "af": "AFN",
    # Oceania
    "au": "AUD", "nz": "NZD", "fj": "FJD", "pg": "PGK",
}


def is_category(c: str) -> bool:
    return c in _CAT_IDS


def category_name(c: str) -> str:
    for x in CATEGORIES:
        if x["id"] == c:
            return x["name"]
    return c
