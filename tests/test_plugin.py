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
os.environ["HERMES_HOME"] = tempfile.mkdtemp()

SECRETS = {}
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
    if "serpapi.com" in url:
        return {"shopping_results": [
            {"title": "Tecno Spark 30 128GB", "price": "₦189,500", "extracted_price": 189500,
             "thumbnail": "https://img.example/spark.jpg", "source": "Jumia", "product_link": "https://x/1", "product_id": "1"},
            {"title": "Tecno Spark 30 Pro", "price": "₦235,000", "extracted_price": 235000,
             "thumbnail": "https://img.example/pro.jpg", "source": "Konga", "product_link": "https://x/2", "product_id": "2"}]}
    if "open.er-api.com" in url:
        return {"result": "success", "rates": {"NGN": 1, "USD": 0.00065, "GHS": 0.0098, "KES": 0.084}}
    if url.endswith("/transaction/initialize"):
        assert body["currency"] in ("NGN", "GHS") and isinstance(body["amount"], int), body
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
assert "shopmate-shopping" in ctx.skills

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
r = call(ctx, "set_location", city="Accra", conv="gh"); assert r["zone"] == "international"
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

# --- live key refuses demo items
SECRETS["PAYSTACK_SECRET_KEY"] = "sk_live_x"
call(ctx, "cart_add", product_id="d-pho1")
r = call(ctx, "checkout", full_name="Ada", email="ada@example.com", phone="080", address="1 Rd", city="Lagos", confirm=True)
assert "demo" in r["error"]

# --- handlers never raise
for name in ctx.tools:
    json.loads(ctx.tools[name](None))
print("ALL TESTS PASSED (%d tools)" % len(ctx.tools))
