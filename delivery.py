"""Location-based delivery pricing.

Fee = base for the distance band + per-km charge + size surcharge per item, from the store's
dispatch point to the customer's location, anywhere in the world. Express costs more and arrives
sooner. Shipments from Nigeria use the NGN tables; everywhere else uses the USD tables. Fees are
converted to the customer's currency for display. Edit the tables to match your logistics partner.
"""

import json
import math
import re
from pathlib import Path

PLACES = json.loads((Path(__file__).parent / "data" / "locations.json").read_text("utf-8"))

RATE_TABLES = {
    "NGN": {
        "same_city": {"base": 1500, "per_km": 0, "eta": (0, 1)},        # within ~25 km of dispatch
        "regional": {"base": 2500, "per_km": 8, "eta": (1, 2)},         # up to 300 km
        "national": {"base": 3500, "per_km": 10, "eta": (2, 5)},        # rest of the country
        "international": {"base": 25000, "per_km": 6, "eta": (7, 14)},  # other countries (courier)
        "size": {"small": 0, "medium": 1000, "large": 4000, "bulky": 12000},
    },
    "USD": {
        "same_city": {"base": 5, "per_km": 0, "eta": (0, 1)},
        "regional": {"base": 8, "per_km": 0.02, "eta": (1, 3)},
        "national": {"base": 12, "per_km": 0.015, "eta": (2, 6)},
        "international": {"base": 35, "per_km": 0.004, "eta": (6, 14)},
        "size": {"small": 0, "medium": 4, "large": 15, "bulky": 60},
    },
}
NGN_ORIGINS = {"NG"}
RATES = RATE_TABLES["NGN"]           # backwards-compatible aliases
SIZE_SURCHARGE = RATES["size"]
EXPRESS = {"multiplier": 1.8, "days_faster": 1}
ROAD_FACTOR = 1.3  # straight-line → road distance

_BULKY = re.compile(r"\b(freezer|fridge|refrigerator|washing machine|generator|air ?conditioner|\bac\b|mattress|sofa|wardrobe|\d{2,3}\s*(kg)|chest)\b", re.I)
_LARGE = re.compile(r"\b(tv|television|microwave|fan|cookware|dumbbell|inflator|engine oil|carton|bag of|\d+\s*l\b|5kg|10kg|laptop|monitor|printer)\b", re.I)
_SMALL = re.compile(r"\b(earbuds?|airpods|phone holder|thermometer|charger|cable|lotion|cream|soap|wipes|vitamins?|tablets|capsules|supplement|paracetamol|watch|case|sneakers?|shoes?|trainers|sandals|slides|t-?shirt|shirt|jeans|dress|cap|hat|socks|perfume|lipstick|phone|iphone|smartphone|book)\b", re.I)
_CAT_SIZE = {"groceries": "medium", "phones": "small", "electronics": "medium", "fashion": "small", "home-kitchen": "large",
             "beauty": "small", "baby": "medium", "health": "small", "sports": "medium", "automotive": "medium"}


def size_class(product: dict) -> str:
    t = product.get("title", "")
    if _BULKY.search(t) and not re.search(r"\b(1|2|3|5)\s*kg\b", t, re.I):
        return "bulky"
    if _LARGE.search(t):
        return "large"
    if _SMALL.search(t):
        return "small"
    return _CAT_SIZE.get(product.get("category"), "medium")


def resolve(city: str = None, state: str = None, country: str = None) -> dict:
    """Find a place by city, area, address or state name — anywhere in the world.

    Bundled offline list first (instant), then OpenStreetMap for everything else."""
    p = _resolve_offline(city, state) if not country or country.upper() == "NG" else None
    if p:
        return p
    from . import geo
    text = ", ".join(x for x in (city, state) if x)
    hit = geo.search(text, country) if text else None
    return dict(hit, key=text.lower()) if hit else None


def _resolve_offline(city: str = None, state: str = None) -> dict:
    for q in (city, state):
        k = (q or "").strip().lower().replace(" state", "")
        if not k:
            continue
        if k in PLACES:
            return dict(PLACES[k], key=k)
        head = k.split(",")[0].strip()
        # Whole-word match only ("Lekki Phase 1" → lekki), so "London" never matches "Ondo".
        hits = [key for key in PLACES if re.search(r"(?<![a-z])%s(?![a-z])" % re.escape(key), head)]
        if hits:
            key = max(hits, key=len)  # most specific: "victoria island" over "victoria"
            return dict(PLACES[key], key=key)
        for key, p in PLACES.items():  # typed prefix: "port harc" → port harcourt
            if len(head) >= 4 and key.startswith(head):
                return dict(p, key=key)
    return None


def _km(a, b) -> float:
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h)) * ROAD_FACTOR


def quote(origin: dict, dest: dict, items, speed: str = "standard") -> dict:
    """items: list of (product, qty). Returns fee (in fee_currency), ETA and breakdown."""
    km = _km(origin, dest)
    table = RATE_TABLES["NGN" if origin.get("country") in NGN_ORIGINS else "USD"]
    cur = "NGN" if origin.get("country") in NGN_ORIGINS else "USD"
    if dest.get("country") != origin.get("country"):
        band = "international"
    elif km <= 25:
        band = "same_city"
    elif km <= 300:
        band = "regional"
    else:
        band = "national"
    r = table[band]
    distance_fee = r["base"] + r["per_km"] * km
    size_fee = 0
    for p, q in items:
        s = table["size"][size_class(p)]
        size_fee += s + (s * 0.5 * (q - 1))  # extra units of the same item cost half
    fee = distance_fee + size_fee
    lo, hi = r["eta"]
    if speed == "express":
        fee *= EXPRESS["multiplier"]
        lo, hi = max(0, lo - EXPRESS["days_faster"]), max(1, hi - EXPRESS["days_faster"])
    step = 100 if cur == "NGN" else 0.5
    fee = math.ceil(fee / step) * step
    fee = int(fee) if cur == "NGN" else round(fee, 2)
    eta = "same day" if hi == 0 else ("%d-%d days" % (lo, hi) if lo else "within %d day%s" % (hi, "s" if hi > 1 else ""))
    return {"fee": fee, "fee_currency": cur, "eta": eta, "band": band.replace("_", " "), "distance_km": round(km),
            "breakdown": {"distance": int(round(distance_fee * (EXPRESS["multiplier"] if speed == "express" else 1))),
                          "size": int(round(size_fee * (EXPRESS["multiplier"] if speed == "express" else 1)))}}


# ---------- precise location & pickup stops ----------

PICKUP_DISCOUNT = 0.6   # pickup costs 60% of doorstep to the same area
_COORD = re.compile(r"(-?\d{1,2}\.\d{2,})\s*[, ]\s*(-?\d{1,3}\.\d{2,})")


def parse_coords(text: str):
    """Pull lat,lon out of a shared location: '6.4474, 3.4723', Google/Apple Maps links (@lat,lon / q=lat,lon / ll=)."""
    if not text:
        return None
    t = str(text).replace("%2C", ",")
    for pat in (r"@(-?\d+\.\d+),(-?\d+\.\d+)", r"[?&](?:q|query|ll|daddr|destination)=(-?\d+\.\d+),\s*(-?\d+\.\d+)",
                r"!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)"):
        m = re.search(pat, t)
        if m:
            break
    else:
        m = _COORD.search(t)
    if not m:
        return None
    lat, lon = float(m.group(1)), float(m.group(2))
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon


def nearest_place(lat: float, lon: float) -> dict:
    """Name a coordinate by its closest known city/area (offline)."""
    here = {"lat": lat, "lon": lon}
    key, p = min(PLACES.items(), key=lambda kv: _km(here, kv[1]))
    return dict(p, key=key, near_km=round(_km(here, p) / ROAD_FACTOR, 1))


def from_coords(lat: float, lon: float) -> dict:
    near = nearest_place(lat, lon)
    if near["near_km"] <= 15:
        return {"name": near["name"], "state": near.get("state"), "country": near["country"], "lat": lat, "lon": lon,
                "precise": True, "nearest_known": near["name"]}
    from . import geo
    hit = geo.reverse(lat, lon)
    if hit and hit.get("country"):
        return dict(hit, lat=lat, lon=lon, precise=True, nearest_known=hit.get("city") or hit["name"])
    return {"name": "%s area (%.0f km away)" % (near["name"], near["near_km"]), "state": near.get("state"),
            "country": near["country"], "lat": lat, "lon": lon, "precise": True, "nearest_known": near["name"]}


def local_origin(loc: dict) -> dict:
    """Dispatch from the centre of the customer's own city (local-merchant fulfilment)."""
    city = loc.get("city") or loc.get("nearest_known") or loc.get("name")
    o = resolve(city, None, loc.get("country")) if city else None
    return o or dict(loc)


def _load_stops():
    try:
        from .store import _data_dir
        custom = _data_dir() / "pickup_stops.json"
        if custom.exists():
            return json.loads(custom.read_text("utf-8"))["stops"], False
    except Exception:
        pass
    return json.loads((Path(__file__).parent / "data" / "pickup_stops.json").read_text("utf-8"))["stops"], True


def nearest_stops(loc: dict, n: int = 3):
    stops, sample = _load_stops()
    ranked = sorted(stops, key=lambda s: _km(loc, s))[:n]
    return [dict(s, distance_from_customer_km=round(_km(loc, s), 1), sample=sample) for s in ranked]


def find_stop(stop_id: str):
    stops, sample = _load_stops()
    for s in stops:
        if s["id"] == stop_id:
            return dict(s, sample=sample)
    return None


def pickup_quote(origin, stop, items, speed="standard"):
    q = quote(origin, dict(stop, country=stop.get("country") or origin["country"]), items, speed)
    step = 100 if q["fee_currency"] == "NGN" else 0.5
    q["fee"] = math.ceil(q["fee"] * PICKUP_DISCOUNT / step) * step
    q["fee"] = int(q["fee"]) if q["fee_currency"] == "NGN" else round(q["fee"], 2)
    return q
