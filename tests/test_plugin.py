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
CHARGE = {"mismatch": False}
TRANSFER = {"fail": False, "status": "success"}
VERIFY = {"mismatch": False}


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
        inits = [c[1] for c in CALLS if isinstance(c, tuple) and c[0] == "paystack_init"]
        charges = [c[1] for c in CALLS if isinstance(c, tuple) and c[0] == "charge_auth"]
        ref = url.rsplit("/", 1)[-1]
        if charges and (ref.endswith("-CHG") or not inits):
            body = charges[-1]
            amount, currency = body["amount"], body["currency"]
        elif inits:
            body = inits[-1]
            amount, currency = body["amount"], body["currency"]
        else:
            amount, currency = 0, "NGN"
        if VERIFY.get("mismatch"):
            amount = amount + 99
        auth = {"authorization_code": "AUTH_uh8bcl3zbn", "reusable": True, "signature": "SIG_yEXu7dLBeqG0kU7g95Ke",
                "last4": "4081", "brand": "visa", "bank": "TEST BANK", "bin": "408408",
                "exp_month": "12", "exp_year": "2030", "card_type": "visa"}
        return {"data": {"status": "success" if PAID["paystack"] else "ongoing", "amount": amount,
                         "currency": currency, "reference": ref, "authorization": auth, "id": 4099}}
    if url.endswith("/refund") and method == "POST":
        CALLS.append(("paystack_refund", body))
        if not REFUNDED["ok"]:
            return {"status": False, "message": "refund failed"}
        return {"status": True, "data": {"id": 99, "transaction": body.get("transaction"), "status": "processed"}}
    if url.endswith("/transaction/charge_authorization") and method == "POST":
        CALLS.append(("charge_auth", body))
        if body.get("authorization_code") == "AUTH_BAD":
            return {"status": True, "data": {"status": "failed", "gateway_response": "Declined", "amount": body["amount"],
                    "currency": body["currency"], "reference": body["reference"]}}
        if CHARGE.get("mismatch"):
            return {"status": True, "data": {"status": "success", "amount": body["amount"] + 50, "currency": body["currency"],
                    "reference": body["reference"], "authorization": {
                        "authorization_code": body["authorization_code"], "reusable": True, "signature": "SIG_x",
                        "last4": "4081", "brand": "visa", "bank": "TEST BANK", "bin": "408408"}}}
        return {"status": True, "data": {"status": "success", "amount": body["amount"], "currency": body["currency"],
                "reference": body["reference"], "id": 777,
                "authorization": {"authorization_code": body["authorization_code"], "reusable": True,
                                  "signature": "SIG_" + body["authorization_code"], "last4": "4081", "brand": "visa",
                                  "bank": "TEST BANK", "bin": "408408", "exp_month": "12", "exp_year": "2030",
                                  "card_type": "visa"}}}
    if "/bank/resolve" in url:
        CALLS.append(("resolve", url))
        if "0000000000" in url:
            return {"status": False, "message": "Account not found"}
        return {"status": True, "data": {"account_number": "0123456789", "account_name": "ADA OBI"}}
    if url.startswith("https://api.paystack.co/bank?") or url.rstrip("/").endswith("/bank"):
        return {"status": True, "data": [{"name": "Guaranty Trust Bank", "code": "058"},
                                         {"name": "Access Bank", "code": "044"}]}
    if url.endswith("/transferrecipient") and method == "POST":
        CALLS.append(("recipient", body))
        return {"status": True, "data": {"recipient_code": "RCP_test", "name": body.get("name"),
                                         "details": {"account_number": body.get("account_number"),
                                                     "account_name": body.get("name"), "bank_code": body.get("bank_code")}}}
    if url.endswith("/transfer") and method == "POST":
        CALLS.append(("transfer", body))
        if TRANSFER.get("fail"):
            return {"status": False, "message": "Insufficient balance"}
        return {"status": True, "data": {"transfer_code": "TRF_test", "reference": body["reference"],
                                         "status": TRANSFER.get("status", "success"), "amount": body["amount"],
                                         "recipient": body["recipient"]}}
    if "/transfer/" in url and method != "POST":
        code = url.rsplit("/", 1)[-1]
        return {"status": True, "data": {"transfer_code": code, "reference": "SM-TRF-dummy",
                                         "status": TRANSFER.get("status", "success"), "amount": 10000}}
    if "/decision/bin/" in url:
        return {"status": True, "data": {"bin": "408408", "brand": "Visa", "bank": "Test Bank",
                                         "country_code": "NG", "card_type": "debit"}}
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
        self.tools, self.skills, self.hooks = {}, {}, {}

    def register_tool(self, name, toolset, schema, handler, **kw):
        assert schema["name"] == name
        self.tools[name] = handler

    def register_hook(self, name, fn, **kw):
        self.hooks.setdefault(name, []).append(fn)

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
assert manifest.get("provides_hooks") == ["pre_tool_call"]
assert "pre_tool_call" in ctx.hooks and tools.pre_tool_call in ctx.hooks["pre_tool_call"]
assert "shopping" in ctx.skills and ":" not in "shopping"
assert len(ctx.tools) == 17
assert "config_schema" in manifest and "allow_transfers" in manifest["config_schema"]
assert "allow_cross_conversation_lookup" in manifest["config_schema"]

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

# ========== v0.5 Paystack: verify, saved cards, transfers, webhooks ==========
from shopmate import webhooks, payments as paymod
import hashlib, hmac

# --- verify saves authorization + marks paid only on match
SECRETS.update(PAYSTACK_SECRET_KEY="sk_test_x", SERPAPI_API_KEY="k")
PAID["paystack"] = False
VERIFY["mismatch"] = False
call(ctx, "cart_clear", conv="v5")
call(ctx, "set_location", city="Lekki Phase 1", conv="v5")
r = call(ctx, "shop_search", query="tecno spark 30", conv="v5")
live_pid = r["results"][0]["id"]
call(ctx, "cart_add", product_id=live_pid, conv="v5")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", confirm=True, conv="v5")
oid_v = r["order_id"]
assert r["status"] == "awaiting_payment"
# pending verify
s = call(ctx, "order_status", order_id=oid_v, refresh=True, conv="v5")
assert s["payment_status"] == "pending", s
# success + match → paid + card saved
PAID["paystack"] = True
s = call(ctx, "order_status", order_id=oid_v, refresh=True, conv="v5")
assert s["payment_status"] == "paid" and s.get("card", {}).get("last4") == "4081", s
cards = call(ctx, "saved_cards", email="ada@example.com", conv="v5")
assert cards["count"] >= 1 and cards["cards"][0]["last4"] == "4081", cards
assert "authorization_code" not in cards["cards"][0]
assert "signature" not in cards["cards"][0]
card_id = cards["cards"][0]["card_id"]
assert card_id and card_id.startswith("card_") and "AUTH_" not in card_id, cards
# email alone from another conversation is not enough
assert call(ctx, "saved_cards", email="ada@example.com", conv="stranger")["count"] == 0

# amount mismatch must NOT mark paid
call(ctx, "cart_add", product_id=live_pid, conv="v5m")
PAID["paystack"] = True
VERIFY["mismatch"] = True
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", confirm=True, conv="v5m")
oid_m = r["order_id"]
s = call(ctx, "order_status", order_id=oid_m, refresh=True, conv="v5m")
assert s["payment_status"] != "paid", s
VERIFY["mismatch"] = False

# --- charge authorization (saved card) — same conversation that paid
# typing the email in another conversation must not charge this card
call(ctx, "cart_clear", conv="v5c")
call(ctx, "set_location", city="Lekki Phase 1", conv="v5c")
call(ctx, "cart_add", product_id=live_pid, conv="v5c")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", payment_method="saved_card", card_id=card_id, confirm=True, conv="v5c")
assert "error" in r and "conversation" in r["error"].lower(), r

call(ctx, "cart_clear", conv="v5")
call(ctx, "set_location", city="Lekki Phase 1", conv="v5")
call(ctx, "cart_add", product_id=live_pid, conv="v5")
# preview
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", payment_method="saved_card", card_id=card_id, conv="v5")
assert r["status"] == "needs_confirmation" and "4081" in r["summary"]["pay_with"], r
assert r["summary"].get("card", {}).get("last4") == "4081"
assert r["summary"].get("card", {}).get("card_id") == card_id
assert "authorization_code" not in (r["summary"].get("card") or {})
# confirm charge
PAID["paystack"] = True
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", payment_method="saved_card", card_id=card_id, confirm=True, conv="v5")
assert r["status"] == "paid", r
assert any(isinstance(c, tuple) and c[0] == "charge_auth" for c in CALLS)
# charged body must send the auth code to Paystack, but the tool result must not echo it
charge_bodies = [c[1] for c in CALLS if isinstance(c, tuple) and c[0] == "charge_auth"]
assert charge_bodies and charge_bodies[-1]["authorization_code"].startswith("AUTH_")
assert "authorization_code" not in r
assert (r.get("card") or {}).get("card_id") == card_id

# charge amount mismatch (same conversation, card still present)
CHARGE["mismatch"] = True
call(ctx, "cart_add", product_id=live_pid, conv="v5")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", payment_method="saved_card", card_id=card_id, confirm=True, conv="v5")
assert r["status"] != "paid", r
CHARGE["mismatch"] = False

# remove card — only from the owning conversation
assert call(ctx, "saved_cards", email="ada@example.com", action="remove", card_id=card_id, conv="stranger").get("error")
rm = call(ctx, "saved_cards", email="ada@example.com", action="remove", card_id=card_id, conv="v5")
assert rm["removed"] is True
left = call(ctx, "saved_cards", email="ada@example.com", conv="v5")
assert all(c["card_id"] != card_id for c in left["cards"]), left

# missing keys for saved card
SECRETS.pop("PAYSTACK_SECRET_KEY", None)
# re-save a card manually for structure
bob_pub = store.save_card("bob@example.com", {"authorization_code": "AUTH_x", "reusable": True, "signature": "SIG_bob",
                                    "last4": "4242", "brand": "visa", "bin": "408408"}, conversation_id="v5nk")
bob_card_id = bob_pub["card_id"]
assert bob_card_id.startswith("card_") and bob_card_id != "AUTH_x" and bob_card_id != "SIG_bob"
call(ctx, "cart_add", product_id=live_pid, conv="v5nk")
call(ctx, "set_location", city="Lekki", conv="v5nk")
r = call(ctx, "checkout", full_name="Bob", email="bob@example.com", phone="080", address="1 Rd", city="Lekki",
         payment_method="saved_card", card_id=bob_card_id, confirm=True, conv="v5nk")
assert "not configured" in r.get("error", ""), r
# authorization_code must not work as card_id
r = call(ctx, "checkout", full_name="Bob", email="bob@example.com", phone="080", address="1 Rd", city="Lekki",
         payment_method="saved_card", card_id="AUTH_x", confirm=True, conv="v5nk")
assert "error" in r, r
SECRETS["PAYSTACK_SECRET_KEY"] = "sk_test_x"

# --- bank transfer refunds (opt-in plugin setting, not env)
SETTINGS.pop("allow_transfers", None)
# need a paid order
store.save_card("ada@example.com", {"authorization_code": "AUTH_uh8bcl3zbn", "reusable": True,
                                    "signature": "SIG_yEXu7dLBeqG0kU7g95Ke", "last4": "4081", "brand": "visa", "bin": "408408"},
                conversation_id="v5")
# use existing paid oid_v — env var must no longer enable transfers
import os
os.environ["SHOPMATE_ALLOW_TRANSFERS"] = "1"
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", conv="v5")
assert "disabled" in r.get("error", "").lower() or "allow_transfers" in r.get("error", ""), r
os.environ.pop("SHOPMATE_ALLOW_TRANSFERS", None)

SETTINGS["allow_transfers"] = True
# without bank details → bank list
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer", conv="v5")
assert "bank_code" in r.get("error", "") and r.get("banks"), r
# resolve preview
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", conv="v5")
assert r["status"] == "needs_confirmation" and r["summary"]["account_name"] == "ADA OBI", r
# confirm without account_name is rejected
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", confirm=True, conv="v5")
assert "account_name" in r.get("error", "").lower() and "required" in r.get("error", "").lower(), r
# wrong confirmed name
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", account_name="WRONG PERSON", confirm=True, conv="v5")
assert "does not match" in r.get("error", ""), r
# success transfer
TRANSFER["status"] = "success"
r = call(ctx, "request_refund", order_id=oid_v, reason="want bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", account_name="ADA OBI", confirm=True, conv="v5")
assert r["status"] == "refunded" and r["account_name"] == "ADA OBI", r
# duplicate blocked
r = call(ctx, "request_refund", order_id=oid_v, reason="again", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", account_name="ADA OBI", confirm=True, conv="v5")
assert "already" in r.get("error", "").lower(), r

# amount cap
# fresh paid order
PAID["paystack"] = True
VERIFY["mismatch"] = False
call(ctx, "cart_add", product_id=live_pid, conv="v5cap")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", confirm=True, conv="v5cap")
oid_cap = r["order_id"]
call(ctx, "order_status", order_id=oid_cap, refresh=True, conv="v5cap")
order_cap = store.get_order(oid_cap)
r = call(ctx, "request_refund", order_id=oid_cap, reason="too much", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", amount=float(order_cap["charge"]) + 1000, confirm=True, conv="v5cap")
assert "paid amount" in r.get("error", "").lower() or "must be" in r.get("error", "").lower(), r

# transfer initiate failure
PAID["paystack"] = True
call(ctx, "cart_add", product_id=live_pid, conv="v5tf")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", confirm=True, conv="v5tf")
oid_tf = r["order_id"]; call(ctx, "order_status", order_id=oid_tf, refresh=True, conv="v5tf")
TRANSFER["fail"] = True
r = call(ctx, "request_refund", order_id=oid_tf, reason="bank", refund_method="bank_transfer",
         bank_code="058", account_number="0123456789", account_name="ADA OBI", confirm=True, conv="v5tf")
assert "failed" in r.get("error", "").lower(), r
TRANSFER["fail"] = False
SETTINGS.pop("allow_transfers", None)

# --- webhooks
# rebuild a pending order then charge.success webhook
PAID["paystack"] = False
call(ctx, "cart_add", product_id=live_pid, conv="v5wh")
r = call(ctx, "checkout", full_name="Ada", email="wh@example.com", phone="08011112222",
         address="1 Rd", city="Lekki", confirm=True, conv="v5wh")
oid_wh = r["order_id"]
order_wh = store.get_order(oid_wh)
amount_kobo = int(round(order_wh["charge"] * 100))
body = json.dumps({"event": "charge.success", "data": {
    "reference": oid_wh, "status": "success", "amount": amount_kobo, "currency": order_wh["pay_currency"],
    "id": 999, "authorization": {"authorization_code": "AUTH_wh", "reusable": True, "signature": "SIG_wh",
                                 "last4": "1111", "brand": "mastercard", "bin": "539983", "bank": "GTBank"},
    "customer": {"email": "wh@example.com"},
}}).encode()
sig = hmac.new(b"sk_test_x", body, hashlib.sha512).hexdigest()
# bad sig
bad = webhooks.verify_and_handle(body, "0" * 128, "sk_test_x")
assert bad["ok"] is False
good = webhooks.verify_and_handle(body, sig, "sk_test_x")
assert good["ok"] and good.get("payment_status") == "paid", good
assert store.get_order(oid_wh)["payment_status"] == "paid"
wh_cards = store.list_cards("wh@example.com", conversation_id="v5wh")
assert wh_cards[0]["last4"] == "1111"
assert wh_cards[0]["card_id"].startswith("card_") and wh_cards[0]["card_id"] != "AUTH_wh"
assert store.list_cards("wh@example.com", conversation_id="other") == []

# transfer.success webhook
tr_ref = "SM-TRF-" + oid_wh
store.save_transfer(tr_ref, oid_wh, 10, "NGN", "pending", {})
store.update_order(oid_wh, lambda o: dict(o, payment_status="refund_pending",
                                          refund={"status": "refund_pending", "transfer_reference": tr_ref}))
ev = {"event": "transfer.success", "data": {"reference": tr_ref, "transfer_code": "TRF_x", "status": "success"}}
assert webhooks.handle_event(ev)["handled"]
assert store.get_order(oid_wh)["payment_status"] == "refunded"

# refund.processed
PAID["paystack"] = True
call(ctx, "cart_add", product_id=live_pid, conv="v5rp")
r = call(ctx, "checkout", full_name="Ada", email="rp@example.com", phone="080", address="1 Rd", city="Lekki",
         confirm=True, conv="v5rp")
oid_rp = r["order_id"]; call(ctx, "order_status", order_id=oid_rp, refresh=True, conv="v5rp")
webhooks.handle_event({"event": "refund.processed", "data": {"transaction": {"reference": oid_rp}}})
assert store.get_order(oid_rp)["payment_status"] == "refunded"

# BIN helper
SECRETS["PAYSTACK_SECRET_KEY"] = "sk_test_x"
bin_pub = store.save_card("bin@example.com", {"authorization_code": "AUTH_b", "reusable": True, "signature": "SIG_b",
                                    "last4": "9988", "brand": "visa", "bin": "408408"}, conversation_id="bin")
b = call(ctx, "saved_cards", email="bin@example.com", action="bin", card_id=bin_pub["card_id"], conv="bin")
assert b.get("bin", {}).get("brand"), b
assert "authorization_code" not in (b.get("card") or {})

# ========== v0.5.1 review fixes ==========
# Hook: approve directive for saved_card + confirm, and for all refund confirms
hook = tools.pre_tool_call
appr = hook("checkout", {"payment_method": "saved_card", "confirm": True, "card_id": "card_abc"})
assert isinstance(appr, dict) and appr.get("action") == "approve" and appr.get("rule_key") == "shopmate.checkout.saved_card", appr
assert "message" in appr
assert hook("checkout", {"payment_method": "saved_card"}) is None  # preview: no approval yet
assert hook("checkout", {"payment_method": "paystack", "confirm": True}) is None
assert hook("checkout", {"payment_method": "saved_card", "confirm": "true"}) is None  # confirm must be bool True
for method in ("paystack", "bank_transfer", "crypto"):
    appr = hook("request_refund", {"confirm": True, "refund_method": method, "order_id": "SM-X"})
    assert isinstance(appr, dict) and appr["action"] == "approve" and appr["rule_key"] == "shopmate.request_refund", (method, appr)
assert hook("request_refund", {"refund_method": "paystack", "order_id": "SM-X"}) is None

# Cross-conversation refund is refused (even with confirm and the right email)
SETTINGS["allow_transfers"] = True
# oid_cap was paid in v5cap and not refunded (amount cap failed)
r = call(ctx, "request_refund", order_id=oid_cap, reason="steal", confirm=True, conv="stranger")
assert "different conversation" in r.get("error", "").lower(), r
# owner conversation can still preview
r = call(ctx, "request_refund", order_id=oid_cap, reason="owner preview", conv="v5cap")
assert r.get("status") == "needs_confirmation" or r.get("error"), r
SETTINGS.pop("allow_transfers", None)

# order_status email/phone scoped to this conversation (setting off by default)
SETTINGS.pop("allow_cross_conversation_lookup", None)
# c1 placed the first Paystack + crypto orders for ada@example.com
scoped = call(ctx, "order_status", email="ada@example.com", conv="stranger")
assert scoped["count"] == 0, scoped
scoped_phone = call(ctx, "order_status", phone="08011112222", conv="stranger")
assert scoped_phone["count"] == 0, scoped_phone
mine_email = call(ctx, "order_status", email="ada@example.com", conv="c1")
assert mine_email["count"] >= 1, mine_email
# operator can opt in
SETTINGS["allow_cross_conversation_lookup"] = True
cross = call(ctx, "order_status", email="ada@example.com", conv="stranger")
assert cross["count"] >= 1, cross
SETTINGS.pop("allow_cross_conversation_lookup", None)

# Opaque card_id migration of pre-0.5.1 rows
import sqlite3, json as _json
store._reset_paths_for_tests()
# Re-point at a fresh dir, build a legacy cards table, then open via store.
legacy_dir = tempfile.mkdtemp()
os.environ["SHOPMATE_TEST_DATA"] = legacy_dir
store._reset_paths_for_tests()
db = Path(legacy_dir) / "shopmate" / "orders.sqlite"
db.parent.mkdir(parents=True, exist_ok=True)
conn = sqlite3.connect(str(db))
conn.executescript("""
    CREATE TABLE orders (
        order_id TEXT PRIMARY KEY,
        conversation_id TEXT NOT NULL,
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL,
        payment_status TEXT NOT NULL,
        delivery_status TEXT NOT NULL,
        payload TEXT NOT NULL
    );
    CREATE TABLE cards (
        email TEXT NOT NULL,
        signature TEXT NOT NULL,
        authorization_code TEXT NOT NULL,
        last4 TEXT,
        brand TEXT,
        bank TEXT,
        bin TEXT,
        exp_month TEXT,
        exp_year TEXT,
        card_type TEXT,
        reusable INTEGER NOT NULL DEFAULT 1,
        created_at INTEGER NOT NULL,
        payload TEXT NOT NULL,
        PRIMARY KEY (email, signature)
    );
""")
legacy_auth = {"authorization_code": "AUTH_legacy", "reusable": True, "signature": "SIG_legacy",
               "last4": "2222", "brand": "visa", "bank": "TEST", "bin": "408408"}
order_payload = {"order_id": "SM-LEGACY-1", "conversation_id": "legacy-chat",
                 "customer": {"email": "legacy@example.com"}, "authorization": legacy_auth,
                 "payment_status": "paid", "delivery_status": "processing", "created_at": int(time.time())}
conn.execute(
    "INSERT INTO orders(order_id,conversation_id,email,phone,created_at,payment_status,delivery_status,payload) VALUES(?,?,?,?,?,?,?,?)",
    ("SM-LEGACY-1", "legacy-chat", "legacy@example.com", "08011110000", int(time.time()), "paid", "processing",
     _json.dumps(order_payload)),
)
conn.execute(
    "INSERT INTO cards(email,signature,authorization_code,last4,brand,bank,bin,exp_month,exp_year,card_type,reusable,created_at,payload) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
    ("legacy@example.com", "SIG_legacy", "AUTH_legacy", "2222", "visa", "TEST", "408408", "12", "2030", "visa", 1,
     int(time.time()), _json.dumps(legacy_auth)),
)
conn.commit(); conn.close()
migrated = store.list_cards("legacy@example.com", conversation_id="legacy-chat")
assert len(migrated) == 1, migrated
assert migrated[0]["last4"] == "2222"
assert migrated[0]["card_id"] and migrated[0]["card_id"].startswith("card_")
assert migrated[0]["card_id"] not in ("AUTH_legacy", "SIG_legacy")
assert "authorization_code" not in migrated[0]
# backfilled conversation: other chats cannot use the legacy card
assert store.list_cards("legacy@example.com", conversation_id="other") == []
assert store.get_card("legacy@example.com", "AUTH_legacy", conversation_id="legacy-chat") is None
full = store.get_card("legacy@example.com", migrated[0]["card_id"], conversation_id="legacy-chat")
assert full and full["authorization_code"] == "AUTH_legacy"
# skill still has the licensed-pharmacy line
skill = (ROOT / "skills" / "shopping" / "SKILL.md").read_text()
assert "licensed pharmacy" in skill
assert manifest["version"] == "0.5.1"

print("ALL TESTS PASSED (%d tools)" % len(ctx.tools))
