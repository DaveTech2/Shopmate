"""Worldwide place lookup via OpenStreetMap Nominatim (free, no key).

Used only when a place isn't in the bundled offline list. Follows the Nominatim usage policy:
an identifying User-Agent, at most one request per second, and results cached locally.
"""

import threading
import time
import urllib.parse

from . import store
from .providers import _http_json

NOMINATIM = "https://nominatim.openstreetmap.org"
UA = "shopmate-hermes-plugin/0.2 (+https://github.com/DaveTech2/shopmate)"
_LOCK = threading.Lock()
_LAST = [0.0]


def _get(path, params):
    with _LOCK:
        wait = 1.05 - (time.time() - _LAST[0])
        if wait > 0:
            time.sleep(wait)
        _LAST[0] = time.time()
    url = NOMINATIM + path + "?" + urllib.parse.urlencode(dict(params, format="jsonv2", addressdetails=1))
    return _http_json(url, headers={"User-Agent": UA, "Accept-Language": "en"})


def _place(r):
    a = r.get("address") or {}
    name = (a.get("suburb") or a.get("neighbourhood") or a.get("city_district") or a.get("city") or a.get("town")
            or a.get("village") or a.get("county") or r.get("name") or (r.get("display_name") or "").split(",")[0])
    city = a.get("city") or a.get("town") or a.get("village") or a.get("municipality") or a.get("county")
    return {"name": name, "city": city, "state": a.get("state") or a.get("region"),
            "country": (a.get("country_code") or "").upper() or None, "country_name": a.get("country"),
            "lat": float(r["lat"]), "lon": float(r["lon"]), "source": "openstreetmap"}


def _cached(key, fn):
    hit = store.read(lambda d: d.setdefault("geocache", {}).get(key))
    if hit is not None:
        return hit or None
    try:
        val = fn()
    except Exception:
        return None  # network trouble: don't cache, caller falls back
    store.transact(lambda d: d.setdefault("geocache", {}).__setitem__(key, val or {}))
    return val


def search(text: str, country: str = None):
    """Forward-geocode a city, area, landmark or full address anywhere in the world."""
    q = (text or "").strip()
    if not q:
        return None
    key = "f:%s|%s" % (q.lower(), (country or "").lower())

    def fn():
        params = {"q": q, "limit": 1}
        if country:
            params["countrycodes"] = country.lower()
        res = _get("/search", params)
        return _place(res[0]) if res else {}
    return _cached(key, fn)


def reverse(lat: float, lon: float):
    """Name a coordinate (area, city, state, country)."""
    key = "r:%.4f,%.4f" % (lat, lon)

    def fn():
        res = _get("/reverse", {"lat": lat, "lon": lon, "zoom": 16})
        return _place(res) if res and "lat" in res else {}
    return _cached(key, fn)


_IP = {}


def ip_locate():
    """Approximate current location (city level) of the machine Hermes runs on, from its public IP.

    Uses ipapi.co (free, no key). Cached for an hour in memory; nothing is stored."""
    hit = _IP.get("v")
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    try:
        r = _http_json("https://ipapi.co/json/", headers={"User-Agent": UA})
    except Exception:
        return None
    if r.get("error") or r.get("latitude") is None:
        return None
    loc = {"name": r.get("city") or r.get("region") or r.get("country_name"), "city": r.get("city"),
           "state": r.get("region"), "country": (r.get("country_code") or "").upper() or None,
           "country_name": r.get("country_name"), "lat": float(r["latitude"]), "lon": float(r["longitude"]),
           "source": "network", "approximate": True}
    _IP["v"] = (time.time(), loc)
    return loc
