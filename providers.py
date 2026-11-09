"""Product sources: live Google Shopping (SerpAPI) and the bundled demo catalog."""

import hashlib
import json
import re
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


def _token_in(hay: str, w: str) -> bool:
    """Whole-token match so '15' does not hit '150L' or '15.6-inch'. Allows a trailing 's'."""
    return bool(re.search(r"(?<![a-z0-9])%ss?(?![a-z0-9])" % re.escape(w.rstrip("s")), hay))


# Query-token aliases so "toilet paper" hits "Toilet Roll", "soap" hits "Beauty Bar", etc.
TOKEN_ALIASES = {
    "paper": {"paper", "roll", "tissue"},
    "roll": {"roll", "paper", "tissue"},
    "tissue": {"tissue", "paper", "roll"},
    "soap": {"soap", "bar"},
    "bar": {"bar", "soap"},
}


def _token_match(hay: str, w: str) -> bool:
    opts = TOKEN_ALIASES.get(w.rstrip("s"), {w, w.rstrip("s")})
    return any(_token_in(hay, o) or _token_in(hay, o.rstrip("s")) for o in opts)


def search_demo(query: str, category: str = None, max_price: float = None, limit: int = 10):
    """Offline catalog search. Non-numeric tokens are required; bare numbers alone cannot match."""
    raw = (query or "").lower().replace("promax", "pro max").replace("-", " ")
    words = [w for w in re.findall(r"[a-z0-9]+", raw) if len(w) > 1]
    alpha = [w for w in words if not w.isdigit()]
    nums = [w for w in words if w.isdigit()]
    want_pet = any(w in ("pet", "dog", "cat", "animal") for w in alpha)
    scored = []
    for p in DEMO.values():
        if category and p["category"] != category:
            continue
        if max_price and p["price"] > max_price:
            continue
        # Bare "shampoo" should not surface pet shampoo ahead of people's shampoo.
        if p.get("category") == "pets" and not want_pet and "pet" not in alpha:
            if any(w in ("shampoo", "wash", "soap") for w in alpha):
                continue
        title = p["title"].lower()
        brand = (p.get("brand") or "").lower()
        ptype = (p.get("product_type") or "").lower()
        hay = " ".join([title, brand, ptype, p["category"], category_name(p["category"]).lower(),
                        " ".join(p.get("keywords") or [])])
        if alpha:
            if not all(_token_match(hay, w) for w in alpha):
                continue
        elif nums:
            if not all(_token_in(hay, w) for w in nums):
                continue
        elif words:
            continue
        score = 0
        for w in alpha:
            if _token_match(title, w) or _token_match(brand, w) or _token_match(ptype, w):
                score += 3
            elif _token_match(hay, w):
                score += 1
        for w in nums:
            if _token_in(title, w):
                score += 2
            elif _token_in(hay, w):
                score += 1
        # Prefer human toiletries over pet care when both somehow match.
        if p.get("category") == "pets" and not want_pet:
            score -= 5
        if words and score < 1:
            continue
        scored.append((-score, p["price"], p))
    scored.sort(key=lambda t: (t[0], t[1]))
    return [dict(t[2]) for t in scored[:limit]]


def currency_for(country: str) -> str:
    return COUNTRY_CURRENCY.get((country or "").lower(), "USD")


SYMBOL_RE = {
    "NGN": r"(?:₦|NGN|N)\s?([0-9][0-9,]{2,})(?:\.\d{2})?", "GHS": r"(?:GH₵|GHS|GH¢|₵)\s?([0-9][0-9,]*(?:\.\d{2})?)",
    "KES": r"(?:KSh|KES|Ksh)\s?([0-9][0-9,]*)", "ZAR": r"(?:R|ZAR)\s?([0-9][0-9,]*(?:\.\d{2})?)",
    "USD": r"(?:US\$|\$|USD)\s?([0-9][0-9,]*(?:\.\d{2})?)", "GBP": r"(?:£|GBP)\s?([0-9][0-9,]*(?:\.\d{2})?)",
    "EUR": r"(?:€|EUR)\s?([0-9][0-9.,]*)", "INR": r"(?:₹|Rs\.?|INR)\s?([0-9][0-9,]*)",
}
TRUSTED_SHOPS = {
    "ng": ["jumia.com.ng", "konga.com", "slot.ng", "jiji.ng", "pointekonline.com", "kara.com.ng", "3chub.com",
           "spar.com.ng", "supermart.ng", "ebeano.com.ng", "bestbuyng.com", "fouani.com"],
    "gh": ["jumia.com.gh", "tonaton.com", "melcom.com", "compughana.com"],
    "ke": ["jumia.co.ke", "kilimall.co.ke", "phoneplacekenya.com", "carrefour.ke", "jiji.co.ke"],
    "za": ["takealot.com", "makro.co.za", "game.co.za", "incredible.co.za", "checkers.co.za"],
}


def _pid(key):
    return "g-" + hashlib.sha1(str(key).encode()).hexdigest()[:12]


def _price_in(text, currency):
    pat = SYMBOL_RE.get(currency)
    if not pat or not text:
        return None
    for m in re.finditer(pat, text):
        try:
            v = float(m.group(1).replace(",", ""))
        except ValueError:
            continue
        if v >= (100 if currency == "NGN" else 1):
            return v
    return None


def _shopping(api_key, query, country):
    data = _http_json("https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "google_shopping", "q": query, "gl": country, "hl": "en", "api_key": api_key}))
    if data.get("error"):
        raise RuntimeError(data["error"])
    out = []
    for r in data.get("shopping_results", []) or []:
        price = r.get("extracted_price")
        if price is None:
            continue
        link = r.get("product_link") or r.get("link") or ""
        image = r.get("thumbnail") or r.get("serpapi_thumbnail")
        if not image:
            continue  # never show a listing without the exact product photo
        out.append({
            "id": _pid(r.get("product_id") or (r.get("title", "") + link)), "title": r.get("title"),
            "price": price, "price_display": r.get("price"), "currency": currency_for(country),
            "image": image, "merchant": r.get("source"),
            "link": link, "rating": r.get("rating"), "reviews": r.get("reviews"),
            "price_source": "live (Google Shopping)",
        })
    return out


def _web(api_key, query, country):
    """Countries without Google Shopping: read prices from regular Google results (shop listings)."""
    cur = currency_for(country)
    data = _http_json("https://serpapi.com/search.json?" + urllib.parse.urlencode(
        {"engine": "google", "q": query + " price", "gl": country, "hl": "en", "num": 20, "api_key": api_key}))
    if data.get("error"):
        raise RuntimeError(data["error"])
    out = []
    blocks = (data.get("inline_shopping_results") or []) + (data.get("immersive_products") or [])
    for r in blocks:  # product carousels, when Google shows them
        price = r.get("extracted_price") or _price_in(str(r.get("price", "")), cur)
        if not price:
            continue
        link = r.get("link") or r.get("product_link") or ""
        image = r.get("thumbnail")
        if not image:
            continue
        out.append({"id": _pid(r.get("title", "") + link + str(price)), "title": r.get("title"), "price": price,
                    "price_display": r.get("price"), "currency": cur, "image": image,
                    "merchant": r.get("source"), "link": link, "price_source": "live (Google product listing)"})
    shops = TRUSTED_SHOPS.get(country, [])
    for r in data.get("organic_results", []) or []:
        link = r.get("link") or ""
        domain = urllib.parse.urlsplit(link).netloc.lower().removeprefix("www.")
        rich = (r.get("rich_snippet") or {}).get("top") or (r.get("rich_snippet") or {}).get("bottom") or {}
        ext = rich.get("detected_extensions") or {}
        price = ext.get("price") or _price_in(" ".join(map(str, rich.get("extensions") or [])), cur) \
            or _price_in(r.get("title", ""), cur) or _price_in(r.get("snippet", ""), cur)
        if not price:
            continue
        image = r.get("thumbnail")
        if not image:
            continue
        out.append({
            "id": _pid(link), "title": r.get("title"), "price": float(price), "currency": cur,
            "image": image, "merchant": r.get("source") or domain, "link": link,
            "trusted_shop": any(domain.endswith(d) for d in shops),
            "price_source": "live (price from %s listing — confirm on the shop page)" % domain,
        })
    out.sort(key=lambda p: (not p.get("trusted_shop", True), 0))
    return out


def search_live(api_key: str, query: str, country: str = "ng", max_price: float = None, limit: int = 10):
    """Google Shopping first; where it has no results (e.g. Nigeria) fall back to shop listings in Google search."""
    errors = []
    results = []
    for fn in (_shopping, _web):
        try:
            results = fn(api_key, query, country)
        except Exception as e:
            errors.append("%s: %s" % (fn.__name__.strip("_"), e))
            continue
        if results:
            break
    if not results and errors and len(errors) == 2:
        raise RuntimeError("; ".join(errors))
    if max_price:
        results = [r for r in results if r["price"] <= max_price]
    seen, out = set(), []
    for r in results:
        if r["id"] in seen:
            continue
        seen.add(r["id"])
        out.append(r)
    return out[:limit]


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
