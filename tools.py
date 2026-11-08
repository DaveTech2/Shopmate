"""Tool handlers. Every handler returns a JSON string and never raises."""

import json
import re
import time
import uuid
from functools import wraps

from . import payments, providers, store

DELIVERY_ZONES = [
    # (cities, standard fee NGN, standard ETA days)
    ({"lagos", "ikeja", "lekki", "ikorodu", "yaba", "surulere", "ajah"}, 2500, "1-2"),
    ({"abuja", "port harcourt", "ibadan", "abeokuta", "ota"}, 4000, "2-4"),
]
OTHER_ZONE = (5500, "3-6")
EXPRESS_MULTIPLIER = 1.8
FREE_DELIVERY_OVER = 150000
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _ok(**kw):
    return json.dumps(kw, ensure_ascii=False)


def _err(msg, **kw):
    return json.dumps(dict(error=msg, **kw), ensure_ascii=False)


def safe(fn):
    @wraps(fn)
    def wrapper(args, **kwargs):
        try:
            return fn(args or {}, **kwargs)
        except Exception as e:  # never raise into Hermes
            return _err("%s failed: %s" % (fn.__name__, e))
    return wrapper


def _cid(kwargs) -> str:
    return str(kwargs.get("conversation_id") or kwargs.get("session_id") or "default")


SYMBOLS = {"NGN": "₦", "GHS": "GH₵", "KES": "KSh ", "ZAR": "R", "USD": "$", "XOF": "CFA ", "EGP": "E£", "RWF": "RF ", "GBP": "£"}


def _money(x, cur="NGN") -> str:
    cur = (cur or "NGN").upper()
    fmt = "{:,.0f}" if cur in ("NGN", "XOF", "RWF", "KES") else "{:,.2f}"
    return SYMBOLS.get(cur, cur + " ") + fmt.format(x)


def _naira(x) -> str:
    return _money(x, "NGN")


def _find_product(data, pid):
    return providers.DEMO.get(pid) or data["products"].get(pid)


def _cart_summary(data, cid):
    cart = data["carts"].get(cid, {})
    lines, subtotal, base = [], 0, None
    for pid, qty in cart.items():
        p = _find_product(data, pid)
        if not p:
            continue
        cur = p.get("currency") or "NGN"
        base = base or cur
        unit = p["price"] if cur == base else round(p["price"] * providers.fx_rate(cur, base), 2)
        line = unit * qty
        subtotal += line
        lines.append({"id": pid, "title": p["title"], "qty": qty, "unit_price": unit, "currency": base,
                      "line_total": line, "image": p.get("image"), "merchant": p.get("merchant"),
                      "price_source": p.get("price_source")})
    return lines, subtotal


def _base_currency(lines):
    return lines[0]["currency"] if lines else "NGN"


def _quote(city: str, speed: str, subtotal: float):
    c = (city or "").strip().lower()
    fee, eta = OTHER_ZONE
    for cities, f, e in DELIVERY_ZONES:
        if c in cities:
            fee, eta = f, e
            break
    if speed == "express":
        fee = round(fee * EXPRESS_MULTIPLIER, -2)
        lo, hi = eta.split("-")
        eta = "%d-%d" % (max(1, int(lo) - 1), max(1, int(hi) - 1)) if int(hi) > 1 else "same day-1"
    if subtotal >= FREE_DELIVERY_OVER and speed != "express":
        fee = 0
    return fee, eta + " days" if "day" not in eta else eta


# ---------- browsing ----------

@safe
def shop_categories(args, **kwargs):
    return _ok(categories=[
        dict(c, sample_count=sum(1 for p in providers.DEMO.values() if p["category"] == c["id"]))
        for c in providers.CATEGORIES
    ], live_search=bool(providers.get_secret("SERPAPI_API_KEY")))


@safe
def shop_search(args, **kwargs):
    query = (args.get("query") or "").strip()
    category = args.get("category")
    max_price = args.get("max_price")
    limit = max(1, min(int(args.get("limit") or 8), 20))
    if category and not providers.is_category(category):
        return _err("Unknown category", valid=[c["id"] for c in providers.CATEGORIES])
    key = providers.get_secret("SERPAPI_API_KEY")
    if key and query:
        q = query if not category else "%s %s" % (query, providers.category_name(category))
        country = providers.get_secret("SHOPMATE_COUNTRY") or "ng"
        try:
            results = providers.search_live(key, q, country, max_price, limit)
        except Exception as e:
            results, note = providers.search_demo(query, category, max_price, limit), "Live search failed (%s); showing demo catalog." % e
        else:
            def cache(data):
                for r in results:
                    data["products"][r["id"]] = dict(r, cached_at=int(time.time()))
            store.transact(cache)
            note = "Live prices from Google Shopping; prices can change at the merchant."
    else:
        results = providers.search_demo(query, category, max_price, limit)
        note = ("Demo catalog: sample prices, not live. Set SERPAPI_API_KEY for real prices and photos."
                if not key else "Browsing a section from the demo catalog; give a search term for live prices.")
    for r in results:
        r["price_display"] = r.get("price_display") or _money(r["price"], r.get("currency"))
    return _ok(results=results, count=len(results), note=note)


@safe
def shop_product(args, **kwargs):
    pid = args.get("product_id")
    p = store.read(lambda d: _find_product(d, pid))
    if not p:
        return _err("Product not found; search again to refresh results", product_id=pid)
    return _ok(product=dict(p, price_display=p.get("price_display") or _money(p["price"], p.get("currency"))))


# ---------- cart ----------

@safe
def cart_add(args, **kwargs):
    pid, cid = args.get("product_id"), _cid(kwargs)
    qty = int(args["quantity"]) if args.get("quantity") is not None else 1
    if qty < 1 or qty > 99:
        return _err("Quantity must be between 1 and 99")

    def fn(data):
        if not _find_product(data, pid):
            return None
        cart = data["carts"].setdefault(cid, {})
        cart[pid] = min(99, cart.get(pid, 0) + qty)
        return _cart_summary(data, cid)
    res = store.transact(fn)
    if res is None:
        return _err("Product not found; search first and use an id from the results", product_id=pid)
    lines, subtotal = res
    return _ok(cart=lines, subtotal=subtotal, subtotal_display=_money(subtotal, _base_currency(lines)))


@safe
def cart_remove(args, **kwargs):
    pid, cid = args.get("product_id"), _cid(kwargs)
    qty = args.get("quantity")

    def fn(data):
        cart = data["carts"].setdefault(cid, {})
        if pid not in cart:
            return None
        if qty and int(qty) < cart[pid]:
            cart[pid] -= int(qty)
        else:
            del cart[pid]
        return _cart_summary(data, cid)
    res = store.transact(fn)
    if res is None:
        return _err("That item is not in the cart", product_id=pid)
    lines, subtotal = res
    return _ok(cart=lines, subtotal=subtotal, subtotal_display=_money(subtotal, _base_currency(lines)))


@safe
def cart_view(args, **kwargs):
    lines, subtotal = store.read(lambda d: _cart_summary(d, _cid(kwargs)))
    return _ok(cart=lines, items=sum(l["qty"] for l in lines), subtotal=subtotal, subtotal_display=_money(subtotal, _base_currency(lines)))


@safe
def cart_clear(args, **kwargs):
    cid = _cid(kwargs)
    store.transact(lambda d: d["carts"].pop(cid, None))
    return _ok(cart=[], subtotal=0, message="Cart cleared")


# ---------- delivery & checkout ----------

def _delivery_fee(city, speed, subtotal, currency):
    """Zone fees are set in NGN; converted when the cart is priced in another currency."""
    rate = providers.fx_rate("NGN", currency) if currency != "NGN" else 1.0
    fee, eta = _quote(city, speed, subtotal / rate if rate else subtotal)
    return round(fee * rate, 2), eta


@safe
def delivery_quote(args, **kwargs):
    speed = args.get("speed") or "standard"
    if speed not in ("standard", "express"):
        return _err("speed must be standard or express")
    lines, subtotal = store.read(lambda d: _cart_summary(d, _cid(kwargs)))
    cur = _base_currency(lines)
    fee, eta = _delivery_fee(args.get("city"), speed, subtotal, cur)
    return _ok(city=args.get("city"), speed=speed, fee=fee, fee_display=_money(fee, cur), eta=eta,
               free_delivery_over=_naira(FREE_DELIVERY_OVER) + " (standard delivery)")


@safe
def payment_options(args, **kwargs):
    return _ok(
        paystack={"enabled": bool(providers.get_secret("PAYSTACK_SECRET_KEY")),
                  "currencies": sorted(payments.PAYSTACK_CURRENCIES),
                  "note": "Only currencies enabled on the store's Paystack account will work."},
        crypto={"enabled": bool(providers.get_secret("NOWPAYMENTS_API_KEY")),
                "coins": sorted(payments.CRYPTO_COINS),
                "note": "Priced in USD at the live rate, paid in the chosen coin."})


@safe
def checkout(args, **kwargs):
    cid = _cid(kwargs)
    required = ["full_name", "email", "phone", "address", "city"]
    missing = [k for k in required if not str(args.get(k) or "").strip()]
    if missing:
        return _err("Missing delivery details", missing=missing)
    if not _EMAIL.match(args["email"].strip()):
        return _err("Email address looks invalid")
    speed = args.get("speed") or "standard"
    method = (args.get("payment_method") or "paystack").lower()
    if method not in ("paystack", "crypto"):
        return _err("payment_method must be paystack or crypto")

    lines, subtotal = store.read(lambda d: _cart_summary(d, cid))
    if not lines:
        return _err("Cart is empty")
    base = _base_currency(lines)
    fee, eta = _delivery_fee(args["city"], speed, subtotal, base)
    total = round(subtotal + fee, 2)

    if method == "paystack":
        pay_cur = (args.get("currency") or base).upper()
        if pay_cur not in payments.PAYSTACK_CURRENCIES:
            return _err("Paystack doesn't support %s" % pay_cur, supported=sorted(payments.PAYSTACK_CURRENCIES))
        coin = None
    else:
        pay_cur = "USD"
        coin = payments.CRYPTO_COINS.get((args.get("crypto_coin") or "usdt").lower())
        if not coin:
            return _err("Unsupported coin", supported=sorted(payments.CRYPTO_COINS))
    rate = providers.fx_rate(base, pay_cur)
    charge = round(total * rate, 2)

    summary = dict(items=[{"title": l["title"], "qty": l["qty"], "line_total": _money(l["line_total"], base)} for l in lines],
                   subtotal=_money(subtotal, base), delivery_fee=_money(fee, base), delivery_eta=eta,
                   total=_money(total, base), deliver_to="%s, %s, %s" % (args["full_name"], args["address"], args["city"]),
                   pay_with=("Paystack (%s)" % pay_cur) if method == "paystack" else "Crypto (%s)" % coin,
                   amount_to_pay=_money(charge, pay_cur) + ("" if method == "paystack" else " worth of " + coin.upper()))
    if pay_cur != base:
        summary["exchange_rate"] = "1 %s = %s %s (live mid-market rate)" % (base, round(rate, 6), pay_cur)

    if not args.get("confirm"):
        return _ok(status="needs_confirmation", summary=summary,
                   instruction="Show this summary to the customer and call checkout again with confirm=true only after they agree.")

    key = providers.get_secret("PAYSTACK_SECRET_KEY" if method == "paystack" else "NOWPAYMENTS_API_KEY")
    if not key:
        return _err("%s payments are not configured. No order was placed and nothing was charged." % method, summary=summary)
    live_key = (method == "paystack" and key.startswith("sk_live")) or method == "crypto"
    if live_key and any(str(l.get("price_source", "")).startswith("demo") for l in lines):
        return _err("Cart contains demo-catalog items with sample prices; refusing a real charge. Search live products instead.")

    order_id = "SM-" + time.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    order = dict(order_id=order_id, created_at=int(time.time()), conversation_id=cid,
                 customer={k: args[k] for k in required}, speed=speed, items=lines, currency=base,
                 subtotal=subtotal, delivery_fee=fee, total=total, eta=eta, payment_method=method,
                 pay_currency=pay_cur, charge=charge, payment_status="pending", delivery_status="awaiting_payment")
    if method == "paystack":
        pay = payments.paystack_initialize(key, args["email"].strip(), charge, pay_cur, order_id,
                                           {"order_id": order_id, "items": len(lines), "city": args["city"]})
        order.update(payment_url=pay.get("authorization_url"), test_mode=key.startswith("sk_test"))
        out = dict(pay_here=order["payment_url"], test_mode=order["test_mode"])
    else:
        pay = payments.crypto_create(key, charge, coin, order_id, "Shopmate order %s (%d items)" % (order_id, len(lines)))
        order.update(crypto_payment_id=pay["payment_id"], pay_address=pay.get("pay_address"),
                     pay_amount=pay.get("pay_amount"), pay_coin=pay.get("pay_currency"), memo=pay.get("payin_extra_id"))
        out = dict(send_exactly="%s %s" % (pay.get("pay_amount"), str(pay.get("pay_currency")).upper()),
                   to_address=pay.get("pay_address"), memo_or_tag=pay.get("payin_extra_id"),
                   warning="Send on the correct network for %s only; crypto payments can't be reversed." % coin)

    def save(data):
        data["orders"][order_id] = order
        data["carts"].pop(cid, None)
    store.transact(save)
    return _ok(status="awaiting_payment", order_id=order_id, amount=_money(charge, pay_cur), delivery_eta=eta,
               note="Order is not paid until order_status confirms it with the payment provider.", **out)


_CRYPTO_DONE = {"finished", "confirmed"}
_CRYPTO_FAIL = {"failed", "expired", "refunded"}


@safe
def order_status(args, **kwargs):
    oid = (args.get("order_id") or "").strip()
    cid = _cid(kwargs)
    if not oid:
        orders = store.read(lambda d: [
            {"order_id": o["order_id"], "amount": _money(o.get("charge", o["total"]), o.get("pay_currency", "NGN")),
             "payment_status": o["payment_status"], "delivery_status": o["delivery_status"]}
            for o in d["orders"].values() if o.get("conversation_id") == cid])
        return _ok(orders=orders)
    order = store.read(lambda d: d["orders"].get(oid))
    if not order:
        return _err("Order not found", order_id=oid)

    if order["payment_status"] not in ("paid",):
        method = order.get("payment_method", "paystack")
        key = providers.get_secret("PAYSTACK_SECRET_KEY" if method == "paystack" else "NOWPAYMENTS_API_KEY")
        new = None
        if key and method == "paystack":
            tx = payments.paystack_verify(key, oid)
            st = tx.get("status")
            if st == "success" and int(tx.get("amount") or 0) == int(round(order["charge"] * 100)) \
                    and (tx.get("currency") or "").upper() == order["pay_currency"]:
                new = "paid"
            elif st in ("failed", "abandoned", "reversed"):
                new = st
        elif key and method == "crypto":
            st = payments.crypto_status(key, order["crypto_payment_id"]).get("payment_status")
            new = "paid" if st in _CRYPTO_DONE else (st if st in _CRYPTO_FAIL else ("confirming" if st else None))
        if new:
            def upd(data):
                o = data["orders"][oid]
                o["payment_status"] = new
                if new == "paid":
                    o["delivery_status"] = "processing"
                    o["paid_at"] = int(time.time())
                return o
            order = store.transact(upd)

    res = dict(order_id=oid, payment_status=order["payment_status"], delivery_status=order["delivery_status"],
               amount=_money(order.get("charge", order["total"]), order.get("pay_currency", "NGN")), eta=order["eta"],
               items=[{"title": l["title"], "qty": l["qty"]} for l in order["items"]])
    if order["payment_status"] != "paid":
        if order.get("payment_url"):
            res["pay_here"] = order["payment_url"]
        if order.get("pay_address"):
            res["send_exactly"] = "%s %s" % (order["pay_amount"], str(order["pay_coin"]).upper())
            res["to_address"] = order["pay_address"]
    return _ok(**res)
