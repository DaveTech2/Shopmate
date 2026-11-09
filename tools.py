"""Tool handlers. Every handler returns a JSON string and never raises."""

import json
import re
import time
import uuid
from functools import wraps

from . import delivery, payments, providers, store

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
        lines.append({"id": pid, "title": p["title"], "category": p.get("category"), "qty": qty, "unit_price": unit, "currency": base,
                      "line_total": line, "image": p.get("image"), "merchant": p.get("merchant"),
                      "price_source": p.get("price_source")})
    return lines, subtotal


def _base_currency(lines):
    return lines[0]["currency"] if lines else "NGN"


def _home():
    o = delivery.resolve(providers.setting("origin", "lagos"))
    return o or delivery.resolve("lagos")


def _origin(loc=None):
    """Where an order ships from. Customers in the store's home country are served from the `origin` setting.
    Customers abroad are served by local merchants in their own city (`abroad: local`, default),
    or shipped internationally from the home dispatch point (`abroad: ship`)."""
    home = _home()
    if loc and loc.get("country") and loc["country"] != home.get("country"):
        if str(providers.setting("abroad", "local")).lower() != "ship":
            return delivery.local_origin(loc)
    return home


def _search_country(loc):
    if loc and loc.get("country"):
        return loc["country"].lower()
    return str(providers.setting("fallback_country", "ng")).lower()


def _location(data, cid):
    return data.get("locations", {}).get(cid)


def _current_location(cid):
    """Saved location, else the customer's current location detected from the network (if enabled)."""
    loc = store.read(lambda d: _location(d, cid))
    if loc or not providers.setting("auto_locate", True):
        return loc
    from . import geo
    loc = geo.ip_locate()
    if loc:
        store.transact(lambda d: d.setdefault("locations", {}).__setitem__(cid, loc))
    return loc


def _where(loc):
    if not loc:
        return None
    w = loc["name"] + (", " + loc["state"] if loc.get("state") and loc["state"] != loc["name"] else "")
    return w + (" (detected from your network — share a pin or address for exact doorstep pricing)"
                if loc.get("approximate") else "")


def _ship(items, loc, speed="standard", currency="NGN"):
    """Delivery quote for [(product, qty)] to loc, in `currency`."""
    q = delivery.quote(_origin(loc), loc, items, speed)
    if q["fee_currency"] != currency:
        q["fee"] = round(q["fee"] * providers.fx_rate(q["fee_currency"], currency), 2)
    q["fee_display"] = _money(q["fee"], currency)
    q["to"] = loc["name"] + (", " + loc["state"] if loc.get("state") else "")
    return q


def _nearest_stop(loc):
    stops = delivery.nearest_stops(loc, 1)
    if not stops or stops[0]["distance_from_customer_km"] > 40 or stops[0].get("country", "NG") != loc.get("country"):
        return None
    return stops[0]


def _pickup(stop, items, speed="standard", currency="NGN"):
    q = delivery.pickup_quote(_origin(dict(stop, country=stop.get("country", "NG"))), stop, items, speed)
    if q["fee_currency"] != currency:
        q["fee"] = round(q["fee"] * providers.fx_rate(q["fee_currency"], currency), 2)
    q["fee_display"] = _money(q["fee"], currency)
    q["to"] = "Pickup: " + stop["name"]
    return q


def _resolve_input(args):
    """City/area name, coordinates, or a shared maps link / location pin."""
    if args.get("lat") is not None and args.get("lon") is not None:
        return delivery.from_coords(float(args["lat"]), float(args["lon"]))
    c = delivery.parse_coords(args.get("shared_location") or args.get("city") or "")
    if c:
        return delivery.from_coords(*c)
    return delivery.resolve(args.get("city"), args.get("state"), args.get("country"))


def _annotate(products, loc, speed="standard"):
    """Add delivery fee + total to each product so the customer sees the full price."""
    for p in products:
        cur = p.get("currency") or "NGN"
        p["price_display"] = p.get("price_display") or _money(p["price"], cur)
        p["size"] = delivery.size_class(p)
        if loc:
            q = _ship([(p, 1)], loc, speed, cur)
            p["delivery_fee"], p["delivery_display"], p["delivery_eta"] = q["fee"], q["fee_display"], q["eta"]
            p["total_delivered"] = _money(p["price"] + q["fee"], cur)
            stop = _nearest_stop(loc)
            if stop:
                pq = _pickup(stop, [(p, 1)], speed, cur)
                p["pickup_option"] = "%s at %s (%.1f km from you)" % (pq["fee_display"], stop["name"], stop["distance_from_customer_km"])
                p["total_with_pickup"] = _money(p["price"] + pq["fee"], cur)
        else:
            p["delivery_display"] = "set your location to see delivery fee"
    return products


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
        loc0 = _current_location(_cid(kwargs))
        country = _search_country(loc0)
        try:
            results = providers.search_live(key, q, country, max_price, limit)
            fallback = str(providers.setting("fallback_country", "ng")).lower()
            if not results and country != fallback:  # Google Shopping isn't live in every country
                results = providers.search_live(key, q, fallback, max_price, limit)
        except Exception as e:
            results, note = providers.search_demo(query, category, max_price, limit), "Live search failed (%s); showing demo catalog." % e
        else:
            def cache(data):
                for r in results:
                    data["products"][r["id"]] = dict(r, cached_at=int(time.time()))
            store.transact(cache)
            note = ("Live prices (%s) — each item's price_source says where it came from; prices can change "
                    "at the merchant, so confirm on the shop page." % (results[0]["currency"] if results
                                                                       else providers.currency_for(country))) \
                if results else "No live listings found for that search; try different words."
    else:
        results = providers.search_demo(query, category, max_price, limit)
        note = ("Demo catalog: sample prices, not live. Set SERPAPI_API_KEY for real prices and photos."
                if not key else "Browsing a section from the demo catalog; give a search term for live prices.")
    loc = _current_location(_cid(kwargs))
    _annotate(results, loc)
    out = dict(results=results, count=len(results), note=note)
    if results:
        out["best_match"] = results[0]["id"]
    if not loc:
        out["ask_location"] = "Couldn't detect a location; ask the customer for their area or a location pin (set_location)."
    else:
        out["delivering_to"] = _where(loc)
    return _ok(**out)


@safe
def shop_product(args, **kwargs):
    pid = args.get("product_id")
    cid = _cid(kwargs)
    p = store.read(lambda d: _find_product(d, pid))
    loc = _current_location(cid)
    if not p:
        return _err("Product not found; search again to refresh results", product_id=pid)
    p = _annotate([dict(p)], loc)[0]
    if loc:
        p["delivery_express"] = _ship([(p, 1)], loc, "express", p.get("currency") or "NGN")["fee_display"]
    return _ok(product=p)


@safe
def set_location(args, **kwargs):
    cid = _cid(kwargs)
    if not (args.get("city") or args.get("state") or args.get("lat") is not None or args.get("shared_location")):
        from . import geo
        loc = geo.ip_locate()  # "use where I am"
    else:
        loc = _resolve_input(args)
    if not loc:
        return _err("I couldn't place that location. Ask the customer to share their location pin / Google Maps link, "
                    "or name the nearest big city or the state.",
                    examples=["Lekki", "Ikeja", "Abuja", "Port Harcourt", "Kano", "Enugu", "Accra"])
    loc = dict(loc, area=None if loc.get("precise") else args.get("city"))
    store.transact(lambda d: d.setdefault("locations", {}).__setitem__(cid, loc))
    o = _origin(loc)
    sample = delivery.quote(o, loc, [({"title": "", "category": "phones"}, 1)])
    local_cur = providers.currency_for(loc.get("country"))
    sample_fee = sample["fee"] if sample["fee_currency"] == local_cur else \
        round(sample["fee"] * providers.fx_rate(sample["fee_currency"], local_cur), 2)
    return _ok(location=_where(loc), country=loc["country"], approximate=bool(loc.get("approximate")),
               ships_from=o["name"], distance_km=sample["distance_km"], zone=sample["band"],
               typical_eta=sample["eta"], doorstep_small_item_fee=_money(sample_fee, local_cur), local_currency=local_cur,
               precise=bool(loc.get("precise")),
               nearest_pickup_stops=[{"id": s["id"], "name": s["name"], "km_from_you": s["distance_from_customer_km"],
                                      "hours": s.get("hours"), "sample": s["sample"]}
                                     for s in delivery.nearest_stops(loc, 3)
                                     if s["distance_from_customer_km"] <= 40 and s.get("country", "NG") == loc.get("country")],
               note="Every product will now show doorstep delivery and, where available, a cheaper pickup option.")


@safe
def pickup_stops(args, **kwargs):
    loc = _resolve_input(args) if (args.get("city") or args.get("lat") is not None or args.get("shared_location")) \
        else store.read(lambda d: _location(d, _cid(kwargs)))
    if not loc:
        return _err("No location yet; ask the customer to share their location or name their area.")
    stops = delivery.nearest_stops(loc, int(args.get("limit") or 3))
    return _ok(near=loc["name"], stops=[{"id": s["id"], "name": s["name"], "state": s["state"], "hours": s.get("hours"),
                                         "km_from_you": s["distance_from_customer_km"],
                                         "map": "https://maps.google.com/?q=%s,%s" % (s["lat"], s["lon"])} for s in stops],
               sample_data=stops[0]["sample"] if stops else None)


# ---------- cart ----------

MAX_QTY = 1000


@safe
def cart_add(args, **kwargs):
    """Add one product, or many at once (bulk orders): items=[{product_id, quantity}, ...]."""
    cid = _cid(kwargs)
    items = args.get("items") or [{"product_id": args.get("product_id"), "quantity": args.get("quantity")}]
    wanted = []
    for it in items:
        pid = (it or {}).get("product_id")
        qty = int(it["quantity"]) if (it or {}).get("quantity") is not None else 1
        if qty < 1 or qty > MAX_QTY:
            return _err("Quantity must be between 1 and %d" % MAX_QTY, product_id=pid)
        wanted.append((pid, qty))

    def fn(data):
        missing = [pid for pid, _ in wanted if not _find_product(data, pid)]
        if missing:
            return missing
        cart = data["carts"].setdefault(cid, {})
        for pid, qty in wanted:
            cart[pid] = min(MAX_QTY, cart.get(pid, 0) + qty)
        return _cart_summary(data, cid)
    res = store.transact(fn)
    if isinstance(res, list):
        return _err("Product not found; search first and use ids from the results", product_ids=res)
    lines, subtotal = res
    cur = _base_currency(lines)
    out = dict(cart=lines, items=sum(l["qty"] for l in lines), subtotal=subtotal, subtotal_display=_money(subtotal, cur))
    loc = _current_location(cid)
    if loc:
        q = _ship([(l, l["qty"]) for l in lines], loc, "standard", cur)
        out.update(delivery_fee=q["fee_display"], delivery_eta=q["eta"], delivering_to=q["to"],
                   total_delivered=_money(subtotal + q["fee"], cur))
    return _ok(**out)


@safe
def cart_update(args, **kwargs):
    """Set an item's quantity exactly (0 removes it) — handy for bulk orders."""
    pid, cid = args.get("product_id"), _cid(kwargs)
    qty = int(args.get("quantity") if args.get("quantity") is not None else -1)
    if qty < 0 or qty > MAX_QTY:
        return _err("quantity must be between 0 and %d" % MAX_QTY)

    def fn(data):
        cart = data["carts"].setdefault(cid, {})
        if pid not in cart:
            return None
        if qty == 0:
            del cart[pid]
        else:
            cart[pid] = qty
        return _cart_summary(data, cid)
    res = store.transact(fn)
    if res is None:
        return _err("That item is not in the cart", product_id=pid)
    lines, subtotal = res
    return _ok(cart=lines, items=sum(l["qty"] for l in lines), subtotal=subtotal,
               subtotal_display=_money(subtotal, _base_currency(lines)))


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
    cid = _cid(kwargs)
    lines, subtotal = store.read(lambda d: _cart_summary(d, cid))
    loc = _current_location(cid)
    cur = _base_currency(lines)
    out = dict(cart=lines, items=sum(l["qty"] for l in lines), subtotal=subtotal, subtotal_display=_money(subtotal, cur))
    if lines and loc:
        q = _ship([(l, l["qty"]) for l in lines], loc, "standard", cur)
        out.update(delivery_fee=q["fee_display"], delivery_eta=q["eta"], delivering_to=q["to"],
                   total_delivered=_money(subtotal + q["fee"], cur))
    return _ok(**out)


@safe
def cart_clear(args, **kwargs):
    cid = _cid(kwargs)
    store.transact(lambda d: d["carts"].pop(cid, None))
    return _ok(cart=[], subtotal=0, message="Cart cleared")


# ---------- delivery & checkout ----------

@safe
def delivery_quote(args, **kwargs):
    speed = args.get("speed") or "standard"
    if speed not in ("standard", "express"):
        return _err("speed must be standard or express")
    cid = _cid(kwargs)
    lines, subtotal = store.read(lambda d: _cart_summary(d, cid))
    loc = _current_location(cid)
    if args.get("city") or args.get("lat") is not None or args.get("shared_location"):
        loc = _resolve_input(args) or loc
    if not loc:
        return _err("No location yet; ask the customer to share their location or name their area, then call set_location.")
    cur = _base_currency(lines)
    items = [(l, l["qty"]) for l in lines] or [({"title": "", "category": "phones"}, 1)]
    out = {"speed": speed, "doorstep": _ship(items, loc, speed, cur)}
    stop = delivery.find_stop(args["pickup_stop_id"]) if args.get("pickup_stop_id") else _nearest_stop(loc)
    if stop:
        out["pickup"] = dict(_pickup(stop, items, speed, cur), stop_id=stop["id"],
                             km_from_you=round(delivery._km(loc, stop), 1))
    return _ok(**out)


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
    saved = _current_location(cid)
    dmethod = (args.get("delivery_method") or "doorstep").lower()
    if dmethod not in ("doorstep", "pickup"):
        return _err("delivery_method must be doorstep or pickup")
    has_place = args.get("city") or args.get("lat") is not None or args.get("shared_location")
    loc = (_resolve_input(args) if has_place else None) or saved
    if not loc:
        return _err("No delivery location; ask the customer to share their location or name their area.")
    stop = None
    if dmethod == "pickup":
        stop = delivery.find_stop(args["pickup_stop_id"]) if args.get("pickup_stop_id") else _nearest_stop(loc)
        if not stop:
            return _err("No pickup stop near this location; use doorstep delivery.")
        args = dict(args, address=args.get("address") or "Pickup at " + stop["name"])
    args = dict(args, city=args.get("city") or loc.get("area") or loc.get("city") or loc["name"])
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
    street_fallback_note = None
    if dmethod == "doorstep" and args.get("address") and not loc.get("precise"):
        from . import geo  # price to the exact street address, not just the city
        exact = geo.search_address(args["address"], args.get("city") or loc.get("name"), loc.get("country"))
        if exact and delivery._km(loc, exact) <= 80:
            loc = dict(exact, precise=True)
        else:
            street_fallback_note = (
                "Couldn't pin the street address on the map; delivery fee is priced to %s (city/area). "
                "Share a location pin or maps link for an exact doorstep price." % loc["name"]
            )
    base = _base_currency(lines)
    items = [(l, l["qty"]) for l in lines]
    q = _pickup(stop, items, speed, base) if stop else _ship(items, loc, speed, base)
    fee, eta = q["fee"], q["eta"]
    total = round(subtotal + fee, 2)

    if method == "paystack":
        if args.get("currency"):
            pay_cur = args["currency"].upper()
        else:  # customer's local currency if Paystack takes it, else the cart's, else USD (international cards)
            local = providers.currency_for(loc.get("country"))
            pay_cur = next((c for c in (local, base, "USD") if c in payments.PAYSTACK_CURRENCIES), "USD")
        if pay_cur not in payments.PAYSTACK_CURRENCIES:
            return _err("Paystack doesn't support %s; use one of these or pay with crypto" % pay_cur, supported=sorted(payments.PAYSTACK_CURRENCIES))
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
                   total=_money(total, base), deliver_to=("%s — %s" % (args["full_name"], q["to"])) if stop else "%s, %s, %s" % (args["full_name"], args["address"], q["to"]),
                   delivery_method=dmethod, delivery_distance_km=q["distance_km"],
                   pay_with=("Paystack (%s)" % pay_cur) if method == "paystack" else "Crypto (%s)" % coin,
                   amount_to_pay=_money(charge, pay_cur) + ("" if method == "paystack" else " worth of " + coin.upper()))
    if street_fallback_note:
        summary["location_note"] = street_fallback_note
    if pay_cur != base:
        summary["exchange_rate"] = "1 %s = %s %s (live mid-market rate)" % (base, round(rate, 6), pay_cur)

    if args.get("confirm") is not True:
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
                 customer={k: args[k] for k in required}, speed=speed, delivery_method=dmethod,
                 pickup_stop=stop and {"id": stop["id"], "name": stop["name"]},
                 location={"lat": loc["lat"], "lon": loc["lon"], "name": loc["name"]}, items=lines, currency=base,
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
