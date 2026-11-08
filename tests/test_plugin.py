"""Offline end-to-end test: stub Hermes, secrets and HTTP; run a full shopping flow.

Run from the repo's parent directory:  python3 -I shopmate/tests/test_plugin.py
"""

import importlib
import json
import os
import sys
import tempfile
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

PAID = {"paystack": False, "crypto": "waiting"}
CALLS = []


def fake_http(url, data=None, headers=None, method=None):
    CALLS.append(url)
    body = json.loads(data) if data else None
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
    if url.endswith("/v1/payment") and method == "POST":
        assert body["price_currency"] == "usd"
        return {"payment_id": 555, "pay_address": "TXyz", "pay_amount": 123.45, "pay_currency": body["pay_currency"], "payment_status": "waiting"}
    if "/v1/payment/555" in url:
        return {"payment_status": PAID["crypto"]}
    raise AssertionError("unexpected url " + url)


providers._http_json = fake_http
importlib.import_module(ROOT.name + ".payments")._http_json = fake_http


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

# --- demo mode (no keys)
r = call(ctx, "shop_categories"); assert len(r["categories"]) == 10 and r["live_search"] is False
r = call(ctx, "shop_search", category="phones"); assert r["count"] == 5 and "Demo" in r["note"]
assert "ask_location" in r and "set your location" in r["results"][0]["delivery_display"]
r = call(ctx, "set_location", city="Atlantis"); assert "error" in r
r = call(ctx, "set_location", city="Lekki Phase 1"); assert r["location"] == "Lekki, Lagos", r
near = call(ctx, "shop_search", query="rice")
assert near["results"][0]["id"] == "d-gro3" and near["best_match"] == "d-gro3"
assert near["results"][0]["size"] == "bulky" and near["results"][0]["total_delivered"].startswith("₦")
call(ctx, "set_location", city="Kano", conv="far")
far = call(ctx, "shop_search", query="rice", conv="far")
assert far["results"][0]["delivery_fee"] > near["results"][0]["delivery_fee"], (far, near)
earbuds = call(ctx, "shop_search", query="oraimo earbuds")["results"][0]
assert earbuds["size"] == "small" and earbuds["delivery_fee"] < near["results"][0]["delivery_fee"]
r = call(ctx, "shop_search", query="tecno phone"); assert r["results"] and "Tecno" in r["results"][0]["title"]
r = call(ctx, "shop_product", product_id="d-pho1"); assert "delivery_express" in r["product"]
r = call(ctx, "set_location", city="Accra", conv="gh"); assert r["zone"] == "same city" and r["local_currency"] == "GHS", r
SETTINGS["abroad"] = "ship"
r = call(ctx, "set_location", city="Accra", conv="gh"); assert r["zone"] == "international", r
del SETTINGS["abroad"]
# precise location from a shared pin / maps link
r = call(ctx, "set_location", lat=6.4480, lon=3.4730, conv="pin")
assert r["precise"] and r["location"].startswith("Lekki") and r["nearest_pickup_stops"][0]["id"] == "lk1", r
r = call(ctx, "set_location", shared_location="https://www.google.com/maps/place/x/@9.0765,7.3986,15z", conv="link")
assert r["precise"] and r["nearest_pickup_stops"], r
pin = call(ctx, "shop_search", query="rice", conv="pin")["results"][0]
assert "pickup_option" in pin and "Lekki Phase 1" in pin["pickup_option"], pin
assert int(pin["total_with_pickup"].replace("₦", "").replace(",", "")) < int(pin["total_delivered"].replace("₦", "").replace(",", ""))
r = call(ctx, "pickup_stops", conv="pin"); assert r["stops"][0]["map"].startswith("https://maps.google.com")
assert call(ctx, "shop_search", query="rice", conv="gh2") and "pickup_option" not in call(ctx, "shop_search", query="rice", conv="gh")["results"][0]
call(ctx, "cart_add", product_id="d-ele1", conv="pin")
q = call(ctx, "delivery_quote", conv="pin"); assert q["pickup"]["fee"] < q["doorstep"]["fee"], q
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", delivery_method="pickup", conv="pin")
assert r["status"] == "needs_confirmation" and "Pickup: Lekki Phase 1" in r["summary"]["deliver_to"], r
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", conv="pin")
assert r["missing"] == ["address"], r
print("lekki rice", near["results"][0]["delivery_display"], "| kano rice", far["results"][0]["delivery_display"], "| lekki earbuds", earbuds["delivery_display"])
r = call(ctx, "cart_add", product_id="d-gro3", quantity=2); assert r["subtotal"] == 124000
v = call(ctx, "cart_view"); assert v["delivering_to"] == "Lekki, Lagos" and "total_delivered" in v
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd")
assert r["status"] == "needs_confirmation" and r["summary"]["delivery_fee"] == v["delivery_fee"], (r, v)
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Lagos", confirm=True)
assert "not configured" in r["error"], r
r = call(ctx, "cart_add", product_id="nope"); assert "error" in r
r = call(ctx, "cart_add", product_id="d-gro3", quantity=0); assert "error" in r

# --- live search + Paystack (test key) in GHS
SECRETS.update(SERPAPI_API_KEY="k", PAYSTACK_SECRET_KEY="sk_test_x", NOWPAYMENTS_API_KEY="np")
call(ctx, "cart_clear")
r = call(ctx, "shop_search", query="tecno spark 30"); assert r["results"][0]["price_source"].startswith("live")
pid = r["results"][0]["id"]
call(ctx, "cart_add", product_id=pid)
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Abuja",
         currency="GHS")
assert "exchange_rate" in r["summary"] and r["summary"]["amount_to_pay"].startswith("GH₵"), r
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Abuja",
         currency="GHS", confirm=True)
oid = r["order_id"]; assert r["pay_here"].startswith("https://checkout.paystack.com")
assert call(ctx, "cart_view")["items"] == 0
assert call(ctx, "order_status", order_id=oid)["payment_status"] == "pending"
PAID["paystack"] = True
s = call(ctx, "order_status", order_id=oid); assert s["payment_status"] == "paid" and s["delivery_status"] == "processing"

# --- crypto checkout
call(ctx, "cart_add", product_id=pid, quantity=2)
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Kano",
         payment_method="crypto", crypto_coin="usdt", confirm=True)
assert r["to_address"] == "TXyz" and "USDTTRC20" in r["send_exactly"], r
coid = r["order_id"]
assert call(ctx, "order_status", order_id=coid)["payment_status"] == "confirming"
PAID["crypto"] = "finished"
assert call(ctx, "order_status", order_id=coid)["payment_status"] == "paid"
assert len(call(ctx, "order_status")["orders"]) == 2
assert call(ctx, "order_status", conv="other")["orders"] == []

# --- Nigeria has no Google Shopping: falls back to shop listings in Google search
r = call(ctx, "shop_search", query="infinix hot 50")
titles = [x["merchant"] for x in r["results"]]
assert len(r["results"]) == 2 and r["results"][0]["price"] == 231500 and "Konga" not in titles[0], r
assert r["results"][1]["price"] == 228999 and r["results"][0]["image"] and "confirm" in r["results"][0]["price_source"]
assert r["results"][0]["total_delivered"], r
print("ng web fallback:", [(x["merchant"], x["price_display"], x.get("delivery_display")) for x in r["results"]])
# --- worldwide: any city / address, local merchants, local currency
r = call(ctx, "set_location", city="Brooklyn, New York", conv="nyc"); assert r["country"] == "US" and r["local_currency"] == "USD", r
r = call(ctx, "shop_search", query="airpods pro", conv="nyc")
ap = r["results"][0]; assert ap["currency"] == "USD" and ap["delivery_display"].startswith("$") and "Best Buy" == ap["merchant"], ap
call(ctx, "cart_add", product_id=ap["id"], conv="nyc")
r = call(ctx, "checkout", full_name="Sam", email="sam@example.com", phone="+1 555", address="1 Main St", conv="nyc")
assert r["summary"]["pay_with"] == "Paystack (USD)" and "exchange_rate" not in r["summary"], r
r = call(ctx, "set_location", lat=51.5010, lon=-0.1416, conv="ldn"); assert r["country"] == "GB" and "Westminster" in r["location"], r
r = call(ctx, "shop_search", query="tecno spark 30", conv="ldn")  # gl=gb empty -> falls back to the store country
assert r["results"] and r["results"][0]["delivery_display"], r
r = call(ctx, "set_location", city="Nowhereville Zzz", conv="x"); assert "error" in r
print("nyc airpods delivery:", ap["delivery_display"])
# --- "I'm in London, I need an iPhone 17 Pro Max"
r = call(ctx, "set_location", city="London", conv="uk"); assert r["country"] == "GB" and r["local_currency"] == "GBP", r
r = call(ctx, "shop_search", query="iphone 17 pro max", conv="uk")
ip = r["results"][0]; assert ip["currency"] == "GBP" and ip["merchant"] == "Currys" and ip["delivery_display"].startswith("£"), ip
call(ctx, "cart_add", product_id=ip["id"], conv="uk")
r = call(ctx, "checkout", full_name="Jo", email="jo@example.com", phone="+44 20", address="1 Baker St", conv="uk")
assert r["summary"]["pay_with"] == "Paystack (USD)" and "exchange_rate" in r["summary"], r
print("london iphone:", ip["price_display"], "+ delivery", ip["delivery_display"], "=", ip["total_delivered"], "| pay", r["summary"]["amount_to_pay"])
# --- live key refuses demo items
SECRETS["PAYSTACK_SECRET_KEY"] = "sk_live_x"
call(ctx, "cart_add", product_id="d-pho1")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Lagos", confirm=True)
assert "demo" in r["error"]

# --- handlers never raise
for name in ctx.tools:
    json.loads(ctx.tools[name](None))
print("ALL TESTS PASSED (%d tools)" % len(ctx.tools))
