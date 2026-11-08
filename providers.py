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
    q = (query or "").lower().split()
    out = []
    for p in DEMO.values():
        if category and p["category"] != category:
            continue
        if max_price and p["price"] > max_price:
            continue
        hay = (p["title"] + " " + p["category"]).lower()
        if q and not all(w in hay for w in q):
            continue
        out.append(p)
    out.sort(key=lambda p: p["price"])
    return out[:limit]


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
            "currency": COUNTRY_CURRENCY.get(country, "USD"),
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


COUNTRY_CURRENCY = {"ng": "NGN", "gh": "GHS", "ke": "KES", "za": "ZAR", "us": "USD", "ci": "XOF",
                    "sn": "XOF", "eg": "EGP", "rw": "RWF", "gb": "GBP"}


def is_category(c: str) -> bool:
    return c in _CAT_IDS


def category_name(c: str) -> str:
    for x in CATEGORIES:
        if x["id"] == c:
            return x["name"]
    return c
