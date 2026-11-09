"""Offline end-to-end test: stub Hermes, secrets and HTTP; run a full shopping flow.

Run from the repo's parent directory:  python3 -I shopmate/tests/test_plugin.py
"""

import importlib
import json
import os
import sys
import tempfile
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
os.environ["SHOPMATE_TEST_DATA"] = tempfile.mkdtemp()

SECRETS = {}
SETTINGS = {}
agent = types.ModuleType("agent"); ss = types.ModuleType("agent.secret_scope")
ss.get_secret = lambda n: SECRETS.get(n)
sys.modules["agent"], sys.modules["agent.secret_scope"] = agent, ss

pkg = importlib.import_module(ROOT.name)
providers = importlib.import_module(ROOT.name + ".providers")
tools = importlib.import_module(ROOT.name + ".tools")
store = importlib.import_module(ROOT.name + ".store")

PAID = {"paystack": False, "crypto": "waiting"}
CALLS = []
REFUNDED = {"ok": True}


def fake_http(url, data=None, headers=None, method=None):
    CALLS.append(url)
    body = json.loads(data) if data else None
    if "ipapi.co/json" in url:
        return {"city": "Lekki", "region": "Lagos", "country_code": "NG", "country_name": "Nigeria",
                "latitude": 6.4474, "longitude": 3.4723}
    if "nominatim.openstreetmap.org/search" in url and "Ozumba" in url:
        if "countrycodes" in url:
            return []
        return [{"lat": "6.4350", "lon": "3.4200", "address": {"road": "Ozumba Mbadiwe", "suburb": "Victoria Island",
                 "city": "Lagos", "state": "Lagos", "country": "Nigeria", "country_code": "ng"}}]
    if "nominatim.openstreetmap.org/search" in url and "Admiralty" in url:
        return [{"lat": "6.4310", "lon": "3.4680", "address": {"road": "Admiralty Way", "suburb": "Lekki Phase 1",
                 "city": "Lagos", "state": "Lagos", "country": "Nigeria", "country_code": "ng"}}]
    if "nominatim.openstreetmap.org/search" in url:
        if "Brooklyn" in url:
            return [{"lat": "40.6782", "lon": "-73.9442", "address": {"suburb": "Brooklyn", "city": "New York",
                     "state": "New York", "country": "United States", "country_code": "us"}}]
        if "Westminster" in url or "London" in url:
            return [{"lat": "51.5074", "lon": "-0.1278", "address": {"city": "London", "state": "England",
                     "country": "United Kingdom", "country_code": "gb"}}]
        return []
    if "nominatim.openstreetmap.org/reverse" in url:
        assert headers and "shopmate" in headers.get("User-Agent", "")
        return {"lat": "51.5010", "lon": "-0.1416", "address": {"suburb": "Westminster", "city": "London",
                "state": "England", "country": "United Kingdom", "country_code": "gb"}}
    if "engine=google_shopping" in url and "infinix" in url:
        return {"error": "Google Shopping hasn't returned any results for this query."}
    if "engine=google&" in url and "infinix" in url:
        return {"organic_results": [
            {"title": "Infinix Hot 50 256GB - Jumia Nigeria", "link": "https://www.jumia.com.ng/infinix-hot-50.html",
             "source": "Jumia Nigeria", "thumbnail": "https://img.example/hot50.jpg",
             "rich_snippet": {"top": {"detected_extensions": {"price": 231500}, "extensions": ["₦231,500", "In stock"]}}},
            {"title": "Infinix Hot 50 review", "link": "https://blog.example/hot50", "snippet": "A great phone."},
            {"title": "Infinix Hot 50 | Konga", "link": "https://www.konga.com/product/hot-50",
             "snippet": "Buy Infinix Hot 50 for ₦228,999 with fast delivery in Lagos."}]}
    if "serpapi.com" in url and "gl=us" in url:
        return {"shopping_results": [{"title": "Apple AirPods Pro 2", "price": "$189.99", "extracted_price": 189.99,
                "thumbnail": "https://img.example/ap.jpg", "source": "Best Buy", "product_link": "https://x/ap", "product_id": "ap"}]}
    if "engine=google_shopping" in url and "iphone" in url and "gl=gb" in url:
        return {"shopping_results": [{"title": "Apple iPhone 17 Pro Max 256GB", "price": "£1,199.00",
                "extracted_price": 1199.0, "thumbnail": "https://img.example/ip17.jpg", "source": "Currys",
                "product_link": "https://x/ip17", "product_id": "ip17"}]}
    if "serpapi.com" in url and "gl=gb" in url:
        return {"shopping_results": []}
    if "serpapi.com" in url:
        return {"shopping_results": [
            {"title": "Tecno Spark 30 128GB", "price": "₦189,500", "extracted_price": 189500,
             "thumbnail": "https://img.example/spark.jpg", "source": "Jumia", "product_link": "https://x/1", "product_id": "1"},
            {"title": "Tecno Spark 30 Pro", "price": "₦235,000", "extracted_price": 235000,
             "thumbnail": "https://img.example/pro.jpg", "source": "Konga", "product_link": "https://x/2", "product_id": "2"}]}
    if "open.er-api.com" in url:
        if "/latest/USD" in url:
            return {"result": "success", "rates": {"USD": 1, "NGN": 1540, "GBP": 0.78, "GHS": 15}}
        if "/latest/GBP" in url:
            return {"result": "success", "rates": {"GBP": 1, "USD": 1.28, "NGN": 1980}}
        return {"result": "success", "rates": {"NGN": 1, "USD": 0.00065, "GHS": 0.0098, "KES": 0.084, "GBP": 0.0005}}
    if url.endswith("/transaction/initialize"):
        assert body["currency"] in ("NGN", "GHS", "USD") and isinstance(body["amount"], int), body
        CALLS.append(("paystack_init", body))
        return {"status": True, "data": {"authorization_url": "https://checkout.paystack.com/abc", "reference": body["reference"]}}
    if "/transaction/verify/" in url:
        init = [c[1] for c in CALLS if isinstance(c, tuple) and c[0] == "paystack_init"][-1]
        return {"data": {"status": "success" if PAID["paystack"] else "ongoing", "amount": init["amount"], "currency": init["currency"]}}
    if url.endswith("/refund") and method == "POST":
        CALLS.append(("paystack_refund", body))
        if not REFUNDED["ok"]:
            return {"status": False, "message": "refund failed"}
        return {"status": True, "data": {"id": 99, "transaction": body.get("transaction"), "status": "processed"}}
    if url.endswith("/v1/payment") and method == "POST":
        assert body["price_currency"] == "usd"
        return {"payment_id": 555, "pay_address": "TXyz", "pay_amount": 123.45, "pay_currency": body["pay_currency"], "payment_status": "waiting"}
    if "/v1/payment/555" in url:
        return {"payment_status": PAID["crypto"]}
    raise AssertionError("unexpected url " + url)


providers._http_json = fake_http
importlib.import_module(ROOT.name + ".payments")._http_json = fake_http

import urllib.request
class _MapsRedirect:
    def __init__(self, url): self._url = url
    def geturl(self): return self._url
    def read(self): return b""
    def __enter__(self): return self
    def __exit__(self, *a): pass

def _fake_urlopen(req, timeout=None):
    url = req.full_url if isinstance(req, urllib.request.Request) else req
    if "maps.app.goo.gl" in str(url) or "goo.gl/maps" in str(url):
        CALLS.append(("maps_redirect", str(url)))
        return _MapsRedirect("https://www.google.com/maps/place/Lekki/@6.4474,3.4723,15z")
    raise AssertionError("unexpected urlopen " + str(url))

urllib.request.urlopen = _fake_urlopen
importlib.import_module(ROOT.name + ".geo").time.sleep = lambda s: None


class Ctx:
    def __init__(self):
        self.tools, self.skills = {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema["name"] == name
        self.tools[name] = handler

    def get_config(self, key, default=None):
        return SETTINGS.get(key, default)

    def register_skill(self, name, path):
        self.skills[name] = path


def call(ctx, name, conv="c1", **args):
    out = ctx.tools[name](args, conversation_id=conv)
    assert isinstance(out, str)
    return json.loads(out)


ctx = Ctx(); pkg.register(ctx)
import yaml
manifest = yaml.safe_load((ROOT / "plugin.yaml").read_text())
assert sorted(manifest["provides_tools"]) == sorted(ctx.tools), "manifest/tools mismatch"
assert "shopping" in ctx.skills and ":" not in "shopping"
assert len(ctx.tools) == 16

# --- demo mode (no keys)
r = call(ctx, "shop_categories"); assert len(r["categories"]) == 24 and r["live_search"] is False
SETTINGS["auto_locate"] = False
r = call(ctx, "shop_search", category="phones"); assert r["count"] >= 1 and "Demo" in r["note"]
assert all(p.get("image") and "placehold" not in p["image"] for p in r["results"]), r
assert "ask_location" in r
del SETTINGS["auto_locate"]

# Brand options for toothpaste
r = call(ctx, "shop_search", query="toothpaste", conv="brands")
assert r.get("ask_which") and len(r.get("brand_options") or []) >= 3, r
assert all(o.get("image") for o in r["brand_options"]), r
brands = { (o.get("brand") or "").lower() for o in r["brand_options"] }
assert "close-up" in brands and "colgate" in brands, brands

# Body wash SKUs
r = call(ctx, "shop_search", query="body wash")
assert r["count"] >= 2 and all("wash" in x["title"].lower() for x in r["results"]), r
assert all(x.get("image") for x in r["results"])

# Synonyms: soap → Dove Beauty Bar
r = call(ctx, "shop_search", query="soap")
assert any("dove" in x["title"].lower() for x in r["results"]), r

# Shampoo should not prefer pets (no pet shampoo in catalog; Head & Shoulders first)
r = call(ctx, "shop_search", query="shampoo")
assert r["results"] and "Head & Shoulders" in r["results"][0]["title"], r

# fee_currency stays consistent after FX
call(ctx, "set_location", city="Brooklyn", conv="fx")
shipped = tools._ship([({"title": "x", "category": "toiletries"}, 1)],
                      __import__(ROOT.name + ".delivery", fromlist=["resolve"]).resolve("Brooklyn"),
                      "standard", "NGN")
assert shipped["fee_currency"] == "NGN", shipped
assert shipped["fee_display"].startswith("₦"), shipped

# Auto-locate + checkout needs confirmation
r = call(ctx, "shop_search", query="close-up toothpaste", conv="auto")
assert "Lekki" in r["delivering_to"] and r["results"][0]["image"], r
pid = r["results"][0]["id"]
call(ctx, "cart_add", product_id=pid, conv="auto")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222", address="12 Admiralty Way", conv="auto")
assert r["status"] == "needs_confirmation" and r["summary"]["items"][0].get("image"), r

r = call(ctx, "set_location", conv="here"); assert r["approximate"] and "Lekki" in r["location"], r
r = call(ctx, "set_location", city="Atlantis"); assert "error" in r
r = call(ctx, "set_location", city="Lekki Phase 1"); assert "Lekki" in r["location"], r

# Location-aware delivery on earbuds-sized item vs bulky-ish
near = call(ctx, "shop_search", query="nivea lotion")
assert near["results"][0]["total_delivered"].startswith("₦")
call(ctx, "set_location", city="Kano", conv="far")
far = call(ctx, "shop_search", query="nivea lotion", conv="far")
assert far["results"][0]["delivery_fee"] > near["results"][0]["delivery_fee"], (far, near)

earbuds = call(ctx, "shop_search", query="oraimo")["results"]
# may be empty if no oraimo in catalog — use headphones/earbuds title
if not earbuds:
    earbuds = call(ctx, "shop_search", query="earbuds")["results"]
assert earbuds and earbuds[0]["image"]

r = call(ctx, "shop_product", product_id="d-toi1"); assert r["product"]["image"] and "delivery_express" in r["product"]
r = call(ctx, "set_location", city="Accra", conv="gh"); assert r["local_currency"] == "GHS", r

# precise pin
r = call(ctx, "set_location", lat=6.4480, lon=3.4730, conv="pin")
assert r["precise"] and r["location"].startswith("Lekki"), r
call(ctx, "cart_add", product_id="d-toi1", conv="pin")
cv = call(ctx, "cart_view", conv="pin")
assert cv["cart"][0]["image"] and "placehold" not in cv["cart"][0]["image"], cv
q = call(ctx, "delivery_quote", conv="pin"); assert "doorstep" in q
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="12 Admiralty Way", conv="pin")
assert r["status"] == "needs_confirmation" and r["summary"]["items"][0]["image"], r
print("cart_view image:", cv["cart"][0]["image"][:70])
print("checkout image:", r["summary"]["items"][0]["image"][:70])

# --- live search + Paystack
SECRETS.update(SERPAPI_API_KEY="k", PAYSTACK_SECRET_KEY="sk_test_x", NOWPAYMENTS_API_KEY="np")
call(ctx, "cart_clear")
r = call(ctx, "shop_search", query="tecno spark 30"); assert r["results"][0]["price_source"].startswith("live")
assert r["results"][0]["image"], r
pid = r["results"][0]["id"]
call(ctx, "cart_add", product_id=pid)
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222", address="1 Rd", city="Abuja",
         currency="GHS", confirm=True)
oid = r["order_id"]; assert r["pay_here"].startswith("https://checkout.paystack.com")
assert r["items"][0]["image"]
assert call(ctx, "cart_view")["items"] == 0
assert call(ctx, "order_status", order_id=oid)["payment_status"] == "pending"
PAID["paystack"] = True
s = call(ctx, "order_status", order_id=oid); assert s["payment_status"] == "paid" and s["delivery_status"] == "processing"

# crypto checkout
call(ctx, "cart_add", product_id=pid, quantity=2)
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222", address="1 Rd", city="Kano",
         payment_method="crypto", crypto_coin="usdt", confirm=True)
assert r["to_address"] == "TXyz"
coid = r["order_id"]
PAID["crypto"] = "finished"
assert call(ctx, "order_status", order_id=coid, refresh=True)["payment_status"] == "paid"

# --- fast order lookup by email / phone / conversation
listed = call(ctx, "order_status", email="ada@example.com")
assert listed["count"] >= 2 and all("order_id" in o for o in listed["orders"]), listed
by_phone = call(ctx, "order_status", phone="08011112222")
assert by_phone["count"] >= 2, by_phone
mine = call(ctx, "order_status")  # this conversation
assert mine["count"] >= 1

# Seed 1000 fake orders and measure indexed lookup
t0 = time.perf_counter()
for i in range(1000):
    store.save_order({
        "order_id": "SM-BENCH-%04d" % i,
        "conversation_id": "bench-%d" % (i % 50),
        "customer": {"email": "user%d@example.com" % (i % 100), "phone": "080%08d" % i,
                     "full_name": "Bench", "address": "1 Rd", "city": "Lagos"},
        "created_at": int(time.time()) - i,
        "payment_status": "paid", "delivery_status": "processing",
        "items": [{"title": "Close-Up", "qty": 1, "image": "https://img.example/x.jpg"}],
        "total": 1000, "charge": 1000, "pay_currency": "NGN", "eta": "1 day",
    })
seed_ms = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
hit = store.find_orders(order_id="SM-BENCH-0500")
id_ms = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
hits = store.find_orders(email="user42@example.com", limit=20)
email_ms = (time.perf_counter() - t0) * 1000
t0 = time.perf_counter()
hits2 = store.find_orders(phone="0800000042", limit=5)
# phone norm uses last 10 digits — 0800000042 → 800000042? Actually 0800000042 is 10 digits after strip
phone_ms = (time.perf_counter() - t0) * 1000
assert hit and hit[0]["order_id"] == "SM-BENCH-0500"
assert len(hits) >= 1
print("order index: seed 1000 in %.0fms; by_id %.2fms; by_email %.2fms (%d hits); by_phone %.2fms" % (
    seed_ms, id_ms, email_ms, len(hits), phone_ms))
assert id_ms < 50 and email_ms < 50, (id_ms, email_ms)

# --- refunds
r = call(ctx, "request_refund", order_id=oid, reason="changed mind")
assert r["status"] == "needs_confirmation" and r["summary"]["items"][0].get("image"), r
r = call(ctx, "request_refund", order_id=oid, reason="changed mind", confirm=True)
assert r["status"] == "refunded", r
assert any(isinstance(c, tuple) and c[0] == "paystack_refund" for c in CALLS)
r = call(ctx, "request_refund", order_id=oid, reason="again", confirm=True)
assert "already" in r.get("error", "").lower() or "refund" in r.get("error", "").lower(), r

# crypto manual refund
# mark coid as paid already; request refund with payout address
r = call(ctx, "request_refund", order_id=coid, reason="wrong size", payout_address="TRefundAddr", confirm=True)
assert r["status"] == "refund_pending" and r["payout_address"] == "TRefundAddr", r

# --- support
faq = call(ctx, "contact_support", action="faq", topic="refund")
assert faq["faqs"] and "refund" in faq["faqs"][0]["q"].lower()
SECRETS["SUPPORT_EMAIL"] = "ops@example.com"
ticket = call(ctx, "contact_support", action="open", order_id=oid, issue_type="damaged",
              message="Bottle arrived cracked", email="ada@example.com")
assert ticket["ticket_id"].startswith("TK-") and ticket["operator_contact"]["email"] == "ops@example.com", ticket
st = call(ctx, "contact_support", action="status", ticket_id=ticket["ticket_id"])
assert st["count"] == 1 and st["tickets"][0]["status"] == "open"

# NYC live airpods
r = call(ctx, "set_location", city="Brooklyn, New York", conv="nyc"); assert r["country"] == "US"
r = call(ctx, "shop_search", query="airpods pro", conv="nyc")
ap = r["results"][0]; assert ap["currency"] == "USD" and ap["image"]
call(ctx, "cart_add", product_id=ap["id"], conv="nyc")
r = call(ctx, "checkout", full_name="Sam", email="sam@example.com", phone="+15551212", address="1 Main St", conv="nyc")
assert r["summary"]["items"][0]["image"]

# London iPhone
r = call(ctx, "set_location", city="London", conv="uk")
r = call(ctx, "shop_search", query="iphone 17 pro max", conv="uk")
ip = r["results"][0]; assert ip["currency"] == "GBP" and ip["image"]
print("london iphone:", ip["price_display"], "image", ip["image"][:50])

# live key refuses demo
SECRETS["PAYSTACK_SECRET_KEY"] = "sk_live_x"
call(ctx, "cart_add", product_id="d-toi1")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Lagos", confirm=True)
assert "demo" in r["error"]

# confirm must be boolean True
SECRETS.clear()
call(ctx, "cart_clear", conv="conf")
call(ctx, "set_location", lat=6.4474, lon=3.4723, conv="conf")
call(ctx, "cart_add", product_id="d-pho6", conv="conf")
for conf in (False, "false", "true", 0, 1, None, ""):
    r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080",
             address="1 Rd", city="Lekki", confirm=conf, conv="conf")
    assert r.get("status") == "needs_confirmation", (conf, r)

# maps short link
delivery = importlib.import_module(ROOT.name + ".delivery")
assert delivery.parse_coords("https://maps.app.goo.gl/abc123") == (6.4474, 3.4723)

# handlers never raise
for name in ctx.tools:
    json.loads(ctx.tools[name](None))
print("ALL TESTS PASSED (%d tools)" % len(ctx.tools))
