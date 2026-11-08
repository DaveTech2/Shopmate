"""Location-based delivery pricing.

Fee = base for the distance band + per-km charge + size surcharge per item, from the store's
dispatch city to the customer's location. Express costs more and arrives sooner. All amounts are
NGN and are converted for display when the cart is priced in another currency. Edit RATES to match
your logistics partner.
"""

import json
import math
import re
from pathlib import Path

PLACES = json.loads((Path(__file__).parent / "data" / "locations.json").read_text("utf-8"))

RATES = {
    "same_city": {"base": 1500, "per_km": 0, "eta": (0, 1)},      # within ~25 km of dispatch
    "regional": {"base": 2500, "per_km": 8, "eta": (1, 2)},       # up to 300 km
    "national": {"base": 3500, "per_km": 10, "eta": (2, 5)},      # rest of Nigeria
    "international": {"base": 25000, "per_km": 6, "eta": (7, 14)},  # other countries (courier)
}
SIZE_SURCHARGE = {"small": 0, "medium": 1000, "large": 4000, "bulky": 12000}
EXPRESS = {"multiplier": 1.8, "days_faster": 1}
ROAD_FACTOR = 1.3  # straight-line → road distance

_BULKY = re.compile(r"\b(freezer|fridge|refrigerator|washing machine|generator|air ?conditioner|\bac\b|mattress|sofa|wardrobe|\d{2,3}\s*(kg)|chest)\b", re.I)
_LARGE = re.compile(r"\b(tv|television|microwave|fan|cookware|dumbbell|inflator|engine oil|carton|bag of|\d+\s*l\b|5kg|10kg|laptop|monitor|printer)\b", re.I)
_SMALL = re.compile(r"\b(earbuds?|phone holder|thermometer|charger|cable|lotion|soap|wipes|vitamin|watch|case)\b", re.I)
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


def resolve(city: str = None, state: str = None) -> dict:
    """Find a place by city, area, or state name."""
    for q in (city, state):
        k = (q or "").strip().lower().replace(" state", "")
        if not k:
            continue
        if k in PLACES:
            return dict(PLACES[k], key=k)
        for key, p in PLACES.items():  # loose match: "Lekki Phase 1" → lekki
            if key in k.split(",")[0] or k in key:
                return dict(p, key=key)
    return None


def _km(a, b) -> float:
    r = 6371.0
    la1, lo1, la2, lo2 = map(math.radians, (a["lat"], a["lon"], b["lat"], b["lon"]))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h)) * ROAD_FACTOR


def quote(origin: dict, dest: dict, items, speed: str = "standard") -> dict:
    """items: list of (product, qty). Returns fee in NGN, ETA and breakdown."""
    km = _km(origin, dest)
    if dest["country"] != origin["country"]:
        band = "international"
    elif km <= 25:
        band = "same_city"
    elif km <= 300:
        band = "regional"
    else:
        band = "national"
    r = RATES[band]
    distance_fee = r["base"] + r["per_km"] * km
    size_fee = 0
    for p, q in items:
        s = SIZE_SURCHARGE[size_class(p)]
        size_fee += s + (s * 0.5 * (q - 1))  # extra units of the same item cost half
    fee = distance_fee + size_fee
    lo, hi = r["eta"]
    if speed == "express":
        fee *= EXPRESS["multiplier"]
        lo, hi = max(0, lo - EXPRESS["days_faster"]), max(1, hi - EXPRESS["days_faster"])
    fee = int(math.ceil(fee / 100.0) * 100)
    eta = "same day" if hi == 0 else ("%d-%d days" % (lo, hi) if lo else "within %d day%s" % (hi, "s" if hi > 1 else ""))
    return {"fee": fee, "eta": eta, "band": band.replace("_", " "), "distance_km": round(km),
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
    name = near["name"] if near["near_km"] <= 15 else "%s area (%.0f km away)" % (near["name"], near["near_km"])
    return {"name": name, "state": near.get("state"), "country": near["country"], "lat": lat, "lon": lon,
            "precise": True, "nearest_known": near["name"]}


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
    q = quote(origin, dict(stop, country=origin["country"]), items, speed)
    q["fee"] = int(math.ceil(q["fee"] * PICKUP_DISCOUNT / 100.0) * 100)
    return q
