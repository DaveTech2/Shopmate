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


def _setting_bool(key, default=False) -> bool:
    v = providers.setting(key, default)
    if isinstance(v, bool):
        return v
    if v is None or v == "":
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def pre_tool_call(tool_name, args=None, **kwargs):
    """Hermes pre_tool_call hook: escalate money-moving confirms to a real approval.

    Returns ``{"action": "approve", "message", "rule_key"}`` so Hermes prompts the
    operator. The model-set ``confirm`` flag alone is not a human approval.
    """
    args = args if isinstance(args, dict) else {}
    if tool_name == "checkout" and str(args.get("payment_method") or "").lower() == "saved_card" and args.get("confirm") is True:
        last4 = str(args.get("card_id") or "").strip()
        return {
            "action": "approve",
            "message": "Shopmate will charge a saved card for this checkout (card_id %s). "
                       "Approve only if you asked to pay this way." % (last4 or "unknown"),
            "rule_key": "shopmate.checkout.saved_card",
        }
    if tool_name == "request_refund" and args.get("confirm") is True:
        method = (args.get("refund_method") or "paystack").strip() or "paystack"
        oid = (args.get("order_id") or "").strip() or "this order"
        return {
            "action": "approve",
            "message": "Shopmate will issue a %s refund for %s. Approve only if you asked for this refund." % (method, oid),
            "rule_key": "shopmate.request_refund",
        }
    return None


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
        q["fee_currency"] = currency
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
        q["fee_currency"] = currency
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
        # Several brands of the same product type → ask which one before cart_add.
        types = {}
        for r in results:
            pt = (r.get("product_type") or "").strip().lower()
            if not pt:
                continue
            types.setdefault(pt, []).append(r)
        multi = {pt: items for pt, items in types.items() if len({(i.get("brand") or i["title"]).lower() for i in items}) >= 2}
        if multi or (query and len(results) >= 2 and not category):
            options = []
            seen = set()
            for r in results:
                key = (r.get("brand") or r["title"]).lower()
                if key in seen:
                    continue
                seen.add(key)
                options.append({
                    "id": r["id"], "title": r["title"], "brand": r.get("brand"),
                    "size": r.get("size"), "product_type": r.get("product_type"),
                    "price": r["price"], "price_display": r.get("price_display") or _money(r["price"], r.get("currency") or "NGN"),
                    "currency": r.get("currency") or "NGN", "image": r.get("image"),
                    "total_delivered": r.get("total_delivered"),
                })
            if len(options) >= 2:
                out["ask_which"] = True
                out["brand_options"] = options
                out["clarify"] = (
                    "Several options match — show each with its photo, price and size, then ask which brand "
                    "(and size/variant) the customer wants BEFORE calling cart_add."
                )
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
    email = (args.get("email") or "").strip()
    cid = _cid(kwargs)
    cards = store.list_cards(email, conversation_id=cid) if email else []
    return _ok(
        paystack={"enabled": bool(providers.get_secret("PAYSTACK_SECRET_KEY")),
                  "currencies": sorted(payments.PAYSTACK_CURRENCIES),
                  "note": "Only currencies enabled on the store's Paystack account will work."},
        saved_cards={"enabled": bool(cards), "cards": cards,
                     "note": "Pay with a saved card via payment_method=saved_card and card_id. "
                             "Hermes will ask you to approve the charge."},
        crypto={"enabled": bool(providers.get_secret("NOWPAYMENTS_API_KEY")),
                "coins": sorted(payments.CRYPTO_COINS),
                "note": "Priced in USD at the live rate, paid in the chosen coin."},
        bank_transfers={"enabled": _transfers_allowed(),
                        "note": "NGN bank payouts/refunds require allow_transfers=true in Settings → Plugins (moves operator funds)."})


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
    if method not in ("paystack", "crypto", "saved_card"):
        return _err("payment_method must be paystack, saved_card or crypto")

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

    if method in ("paystack", "saved_card"):
        if args.get("currency"):
            pay_cur = args["currency"].upper()
        else:
            local = providers.currency_for(loc.get("country"))
            pay_cur = next((c for c in (local, base, "USD") if c in payments.PAYSTACK_CURRENCIES), "USD")
        if pay_cur not in payments.PAYSTACK_CURRENCIES:
            return _err("Paystack doesn't support %s; use one of these or pay with crypto" % pay_cur, supported=sorted(payments.PAYSTACK_CURRENCIES))
        coin = None
        if method == "saved_card":
            card_id = (args.get("card_id") or "").strip()
            if not card_id:
                return _err("saved_card checkout needs card_id from payment_options/saved_cards")
            auth = store.get_card(args["email"].strip(), card_id, conversation_id=cid)
            if not auth:
                return _err("No saved card matches that card_id for this conversation", card_id=card_id)
            args = dict(args, _saved_auth=auth, card_last4=auth.get("last4"))
    else:
        pay_cur = "USD"
        coin = payments.CRYPTO_COINS.get((args.get("crypto_coin") or "usdt").lower())
        if not coin:
            return _err("Unsupported coin", supported=sorted(payments.CRYPTO_COINS))
    rate = providers.fx_rate(base, pay_cur)
    charge = round(total * rate, 2)

    summary = dict(items=[{"title": l["title"], "qty": l["qty"], "line_total": _money(l["line_total"], base),
                           "image": l.get("image"), "id": l.get("id")} for l in lines],
                   subtotal=_money(subtotal, base), delivery_fee=_money(fee, base), delivery_eta=eta,
                   total=_money(total, base), deliver_to=("%s — %s" % (args["full_name"], q["to"])) if stop else "%s, %s, %s" % (args["full_name"], args["address"], q["to"]),
                   delivery_method=dmethod, delivery_distance_km=q["distance_km"],
                   pay_with=(("Saved card ending %s (%s)" % ((args.get("card_last4") or "????"), pay_cur))
                             if method == "saved_card" else
                             ("Paystack (%s)" % pay_cur) if method in ("paystack", "saved_card") else "Crypto (%s)" % coin),
                   amount_to_pay=_money(charge, pay_cur) + ("" if method != "crypto" else " worth of " + coin.upper()))
    if street_fallback_note:
        summary["location_note"] = street_fallback_note
    if pay_cur != base:
        summary["exchange_rate"] = "1 %s = %s %s (live mid-market rate)" % (base, round(rate, 6), pay_cur)
    if method == "saved_card" and args.get("_saved_auth"):
        auth = args["_saved_auth"]
        summary["pay_with"] = "Saved %s ending %s (%s)" % ((auth.get("brand") or "card"), auth.get("last4") or "????", pay_cur)
        summary["card"] = store.public_card(auth)

    if args.get("confirm") is not True:
        return _ok(status="needs_confirmation", summary=summary,
                   instruction="Show this summary to the customer and call checkout again with confirm=true only after they agree.")

    key_name = "NOWPAYMENTS_API_KEY" if method == "crypto" else "PAYSTACK_SECRET_KEY"
    key = providers.get_secret(key_name)
    if not key:
        return _err("%s payments are not configured. No order was placed and nothing was charged." % method, summary=summary)
    live_key = (method in ("paystack", "saved_card") and key.startswith("sk_live")) or method == "crypto"
    if live_key and any(str(l.get("price_source", "")).startswith("demo") for l in lines):
        return _err("Cart contains demo-catalog items with sample prices; refusing a real charge. Search live products instead.")

    # Enrich summary with saved card label before charging
    if method == "saved_card":
        auth = args.get("_saved_auth") or {}
        summary["pay_with"] = "Saved %s ending %s (%s)" % ((auth.get("brand") or "card"), auth.get("last4") or "????", pay_cur)
        summary["card"] = store.public_card(auth)

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
        order.update(payment_url=pay.get("authorization_url"), test_mode=key.startswith("sk_test"),
                     payment_reference=order_id)
        out = dict(pay_here=order["payment_url"], test_mode=order["test_mode"],
                   note="After the customer pays, call order_status with refresh=true to verify via Paystack.")
        store.save_order(order)
        store.transact(lambda d: d["carts"].pop(cid, None))
        return _ok(status="awaiting_payment", order_id=order_id, amount=_money(charge, pay_cur), delivery_eta=eta,
                   items=[{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in lines], **out)
    if method == "saved_card":
        auth = args["_saved_auth"]
        # Distinct reference for the charge; verify uses this reference
        pay_ref = order_id + "-CHG"
        tx = payments.paystack_charge_authorization(
            key, args["email"].strip(), charge, pay_cur, auth["authorization_code"], pay_ref,
            {"order_id": order_id, "items": len(lines), "city": args["city"]})
        san = payments.sanitize_authorization(tx.get("authorization") or auth) or {}
        if auth.get("card_id"):
            san["card_id"] = auth["card_id"]
        if cid:
            san["conversation_id"] = cid
        order.update(payment_reference=pay_ref, test_mode=key.startswith("sk_test"),
                     authorization=san)
        store.save_order(order)
        store.transact(lambda d: d["carts"].pop(cid, None))
        order = _apply_paystack_success(order, tx)
        if order.get("payment_status") == "paid":
            view = dict(order.get("authorization") or auth)
            if auth.get("card_id"):
                view["card_id"] = auth["card_id"]
            return _ok(status="paid", order_id=order_id, amount=_money(charge, pay_cur), delivery_eta=eta,
                       card=store.public_card(view),
                       items=[{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in lines],
                       note="Charged saved card; Paystack verify matched amount/currency.")
        return _ok(status=order.get("payment_status") or "pending", order_id=order_id,
                   amount=_money(charge, pay_cur),
                   note="Saved-card charge did not succeed; check order_status.",
                   gateway_response=tx.get("gateway_response"))
    # crypto
    pay = payments.crypto_create(key, charge, coin, order_id, "Shopmate order %s (%d items)" % (order_id, len(lines)))
    order.update(crypto_payment_id=pay["payment_id"], pay_address=pay.get("pay_address"),
                 pay_amount=pay.get("pay_amount"), pay_coin=pay.get("pay_currency"), memo=pay.get("payin_extra_id"))
    out = dict(send_exactly="%s %s" % (pay.get("pay_amount"), str(pay.get("pay_currency")).upper()),
               to_address=pay.get("pay_address"), memo_or_tag=pay.get("payin_extra_id"),
               warning="Send on the correct network for %s only; crypto payments can't be reversed." % coin)
    store.save_order(order)
    store.transact(lambda d: d["carts"].pop(cid, None))
    return _ok(status="awaiting_payment", order_id=order_id, amount=_money(charge, pay_cur), delivery_eta=eta,
               items=[{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in lines],
               note="Order is not paid until order_status confirms it with the payment provider.", **out)


_CRYPTO_DONE = {"finished", "confirmed"}
_CRYPTO_FAIL = {"failed", "expired", "refunded"}


def _transfers_allowed() -> bool:
    """Operator must opt in via the allow_transfers plugin setting (off by default)."""
    return _setting_bool("allow_transfers", False)


def _apply_paystack_success(order, tx):
    """Mark paid only when verify/charge reports success with matching amount+currency; save auth."""
    oid = order["order_id"]
    st = tx.get("status")
    amount_ok = int(tx.get("amount") or 0) == int(round(float(order.get("charge") or 0) * 100))
    currency_ok = (tx.get("currency") or "").upper() == (order.get("pay_currency") or "").upper()
    if st == "success" and amount_ok and currency_ok:
        auth = payments.sanitize_authorization(tx.get("authorization") or {})
        prev_id = (order.get("authorization") or {}).get("card_id")
        if auth and prev_id:
            auth = dict(auth, card_id=prev_id)
        def mut(o):
            o["payment_status"] = "paid"
            o["delivery_status"] = "processing"
            o["paid_at"] = int(time.time())
            o["paystack_transaction_id"] = tx.get("id")
            if auth:
                kept = (o.get("authorization") or {}).get("card_id")
                o["authorization"] = dict(auth, **({"card_id": kept} if kept else {}))
            return o
        updated = store.update_order(oid, mut) or order
        if auth and auth.get("reusable"):
            email = (order.get("customer") or {}).get("email") or ""
            if email:
                store.save_card(email, auth, conversation_id=order.get("conversation_id"))
        return updated
    if st in ("failed", "abandoned", "reversed"):
        def mut(o):
            o["payment_status"] = st
            return o
        return store.update_order(oid, mut) or order
    # amount mismatch — do not mark paid
    if st == "success" and not (amount_ok and currency_ok):
        def mut(o):
            o["payment_note"] = "Paystack success but amount/currency mismatch; not marking paid"
            return o
        return store.update_order(oid, mut) or order
    return order

_REFUNDABLE = {"paid", "processing", "shipped"}  # not delivered / already refunded
_DELIVERED_BLOCK = {"delivered", "completed"}
_REFUND_WINDOW_SECS = 14 * 24 * 3600  # 14 days from paid_at / created_at

FAQ = [
    {"q": "How long does delivery take?",
     "a": "Standard delivery is usually within 1–3 days in the same city, longer for other cities. Express is about 1.8× the fee and roughly a day faster. The exact ETA is on every product and on your order."},
    {"q": "What payment methods do you accept?",
     "a": "Paystack (cards and local methods in NGN, GHS, KES, ZAR, USD, XOF, EGP, RWF — whichever the store enabled) and crypto via NOWPayments (USDT, USDC, BTC, ETH, SOL, BNB, TRX, LTC)."},
    {"q": "What is the refund policy?",
     "a": "Paid orders that are not yet delivered can be refunded within 14 days. Call request_refund with the order id and a reason; confirm=true after the customer agrees. Paystack refunds go back to the original payment method; crypto refunds are paid out manually to an address you provide."},
    {"q": "Where is my order?",
     "a": "Use order_status with your order id, or the email/phone used at checkout, to see payment and delivery status instantly."},
    {"q": "An item arrived damaged or wrong.",
     "a": "Open a support ticket with contact_support (issue_type damaged or wrong_item) and your order id. The store will follow up using the operator contact shown."},
]


def _order_brief(o):
    return {
        "order_id": o["order_id"],
        "amount": _money(o.get("charge", o.get("total", 0)), o.get("pay_currency", "NGN")),
        "payment_status": o.get("payment_status"),
        "delivery_status": o.get("delivery_status"),
        "created_at": o.get("created_at"),
        "items": [{"title": l.get("title"), "qty": l.get("qty"), "image": l.get("image")} for l in o.get("items") or []],
    }


def _refresh_payment(order):
    """Hit the payment provider (Paystack verify or NOWPayments). Saves reusable card auth on success."""
    if order.get("payment_status") in ("paid", "refunded", "refund_pending"):
        return order
    method = order.get("payment_method", "paystack")
    if method in ("paystack", "saved_card"):
        key = providers.get_secret("PAYSTACK_SECRET_KEY")
        if not key:
            return order
        # Prefer the charge reference if saved-card used a distinct ref
        ref = order.get("payment_reference") or order["order_id"]
        tx = payments.paystack_verify(key, ref)
        return _apply_paystack_success(order, tx)
    key = providers.get_secret("NOWPAYMENTS_API_KEY")
    if not key:
        return order
    st = payments.crypto_status(key, order["crypto_payment_id"]).get("payment_status")
    new = "paid" if st in _CRYPTO_DONE else (st if st in _CRYPTO_FAIL else ("confirming" if st else None))
    if not new:
        return order
    oid = order["order_id"]
    def mut(o):
        o["payment_status"] = new
        if new == "paid":
            o["delivery_status"] = "processing"
            o["paid_at"] = int(time.time())
        return o
    return store.update_order(oid, mut) or order


@safe
def order_status(args, **kwargs):
    """Fast indexed lookup by order id, email, phone, or this conversation's recent orders.

    Skips the payment-provider API unless refresh=true (or a single pending order is fetched by id).
    """
    oid = (args.get("order_id") or "").strip()
    email = (args.get("email") or "").strip()
    phone = (args.get("phone") or "").strip()
    refresh = args.get("refresh") is True
    limit = args.get("limit") or 10
    cid = _cid(kwargs)

    cross = _setting_bool("allow_cross_conversation_lookup", False)
    if oid:
        orders = store.find_orders(order_id=oid)
    elif email:
        if cross:
            orders = store.find_orders(email=email, limit=limit)
        else:
            orders = store.find_orders(email=email, conversation_id=cid, limit=limit)
    elif phone:
        if cross:
            orders = store.find_orders(phone=phone, limit=limit)
        else:
            orders = store.find_orders(phone=phone, conversation_id=cid, limit=limit)
    else:
        orders = store.find_orders(conversation_id=cid, limit=limit)

    if oid and not orders:
        return _err("Order not found", order_id=oid)

    # Refresh payment only when asked, or when fetching one pending order by id.
    if orders and (refresh or (oid and len(orders) == 1 and orders[0].get("payment_status") not in ("paid", "refunded", "refund_pending"))):
        orders = [_refresh_payment(o) for o in orders]

    if oid and len(orders) == 1:
        order = orders[0]
        res = dict(order_id=oid, payment_status=order["payment_status"], delivery_status=order["delivery_status"],
                   amount=_money(order.get("charge", order["total"]), order.get("pay_currency", "NGN")),
                   eta=order.get("eta"),
                   items=[{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in order.get("items") or []])
        if order["payment_status"] not in ("paid", "refunded", "refund_pending"):
            if order.get("payment_url"):
                res["pay_here"] = order["payment_url"]
            if order.get("pay_address"):
                res["send_exactly"] = "%s %s" % (order["pay_amount"], str(order["pay_coin"]).upper())
                res["to_address"] = order["pay_address"]
        if order.get("refund"):
            res["refund"] = order["refund"]
        auth = order.get("authorization") or {}
        if auth.get("last4"):
            res["card"] = {"last4": auth.get("last4"), "brand": auth.get("brand"), "bank": auth.get("bank")}
        if order.get("payment_note"):
            res["payment_note"] = order["payment_note"]
        return _ok(**res)

    return _ok(orders=[_order_brief(o) for o in orders], count=len(orders),
               note="Indexed lookup (no payment-provider call). Pass refresh=true to re-check payment.")



@safe
def saved_cards(args, **kwargs):
    """List or remove reusable Paystack authorizations for this conversation."""
    email = (args.get("email") or "").strip()
    if not email or not _EMAIL.match(email):
        return _err("A valid email is required")
    cid = _cid(kwargs)
    action = (args.get("action") or "list").strip().lower()
    if action == "list":
        cards = store.list_cards(email, conversation_id=cid)
        return _ok(email=email, cards=cards, count=len(cards),
                   note="Use payment_method=saved_card with card_id at checkout. "
                        "Hermes will ask you to approve the charge. Cards are bound to this conversation.")
    if action == "remove":
        card_id = (args.get("card_id") or "").strip()
        if not card_id:
            return _err("card_id is required to remove a saved card")
        ok = store.remove_card(email, card_id, conversation_id=cid)
        if not ok:
            return _err("No saved card matched that card_id for this conversation", card_id=card_id)
        return _ok(removed=True, card_id=card_id, cards=store.list_cards(email, conversation_id=cid))
    if action == "bin":
        # Optional Card BIN lookup for display enrichment
        card_id = (args.get("card_id") or "").strip()
        auth = store.get_card(email, card_id, conversation_id=cid) if card_id else None
        bin6 = (args.get("bin") or (auth or {}).get("bin") or "").strip()
        key = providers.get_secret("PAYSTACK_SECRET_KEY")
        if not key:
            return _err("PAYSTACK_SECRET_KEY not configured")
        if not bin6:
            return _err("bin or card_id with a stored BIN required")
        info = payments.resolve_card_bin(key, bin6)
        return _ok(bin=info, card=store.public_card(auth) if auth else None,
                   note="BIN metadata only (brand/bank). BVN and phone resolution are not used.")
    return _err("action must be list, remove or bin")


@safe
def request_refund(args, **kwargs):
    """Two-step refund: preview without confirm, execute with confirm=true.

    refund_method:
      - paystack (default): POST /refund to original card/channel
      - bank_transfer: NGN payout via Transfers API (requires allow_transfers=true)
      - crypto: manual payout address (unchanged)
    For bank_transfer, first call resolves the account and returns the account_name; the
    agent MUST show that name and amount, then confirm=true with the same details.
    """
    oid = (args.get("order_id") or "").strip()
    reason = (args.get("reason") or "").strip()
    if not oid:
        return _err("order_id is required")
    if not reason:
        return _err("reason is required")
    order = store.get_order(oid)
    if not order:
        return _err("Order not found", order_id=oid)
    cid = _cid(kwargs)
    if str(order.get("conversation_id") or "") != cid:
        return _err("This order belongs to a different conversation.", order_id=oid)

    pay_st = order.get("payment_status")
    del_st = order.get("delivery_status")
    if pay_st == "refunded":
        return _err("This order is already refunded.", order_id=oid, payment_status=pay_st, refund=order.get("refund"))
    if pay_st == "refund_pending" and (order.get("refund") or {}).get("transfer_reference"):
        return _err("A bank transfer refund is already in progress.", order_id=oid, refund=order.get("refund"))
    if pay_st != "paid":
        return _err("Only paid orders can be refunded.", payment_status=pay_st)
    if del_st in _DELIVERED_BLOCK:
        return _err("Order is already delivered; open a support ticket instead of an automatic refund.",
                    delivery_status=del_st)
    paid_at = int(order.get("paid_at") or order.get("created_at") or 0)
    if paid_at and (time.time() - paid_at) > _REFUND_WINDOW_SECS:
        return _err("Refund window (14 days) has passed; open a support ticket.", order_id=oid)

    # Determine method
    order_method = order.get("payment_method", "paystack")
    refund_method = (args.get("refund_method") or "").strip().lower()
    if not refund_method:
        if args.get("bank_code") or args.get("account_number"):
            refund_method = "bank_transfer"
        elif order_method == "crypto":
            refund_method = "crypto"
        else:
            refund_method = "paystack"
    if refund_method not in ("paystack", "crypto", "bank_transfer"):
        return _err("refund_method must be paystack, bank_transfer or crypto")

    amount = float(args.get("amount") if args.get("amount") is not None else order.get("charge", order.get("total")))
    paid_amount = float(order.get("charge", order.get("total")))
    if amount <= 0 or amount > paid_amount + 1e-9:
        return _err("Refund amount must be > 0 and <= the paid amount (%s)." % _money(paid_amount, order.get("pay_currency", "NGN")),
                    paid=paid_amount, requested=amount)
    cur = order.get("pay_currency", "NGN")

    # ----- bank transfer path (NGN) -----
    if refund_method == "bank_transfer":
        if not _transfers_allowed():
            return _err(
                "Bank transfers are disabled. Set allow_transfers to true in Settings → Plugins to allow NGN payouts from the Paystack balance.",
                order_id=oid)
        key = providers.get_secret("PAYSTACK_SECRET_KEY")
        if not key:
            return _err("PAYSTACK_SECRET_KEY not configured. No transfer was sent.")
        bank_code = (args.get("bank_code") or "").strip()
        account_number = (args.get("account_number") or "").strip()
        if not bank_code or not account_number:
            banks = []
            try:
                banks = [{"name": b.get("name"), "code": b.get("code")} for b in payments.list_banks(key)[:40]]
            except Exception as e:
                banks = [{"error": str(e)}]
            return _err("bank_code and account_number are required for bank_transfer refunds",
                        banks=banks, note="Pick a bank code, then call again to resolve the account name.")
        # Idempotency reference — one transfer per order
        transfer_ref = "SM-TRF-" + oid
        existing = store.get_transfer(transfer_ref)
        if existing and existing.get("status") in ("success", "pending", "otp", "received"):
            return _err("Transfer already submitted for this order (idempotent reference).",
                        transfer=existing, order_id=oid)
        # Resolve account — show name for customer/operator confirm
        resolved = payments.resolve_account(key, account_number, bank_code)
        account_name = (resolved.get("account_name") or "").strip()
        if not account_name:
            return _err("Could not resolve account name for that bank/account number.")
        # Transfer currency is NGN; convert paid amount if needed
        xfer_amount = amount
        xfer_cur = "NGN"
        if cur != "NGN":
            xfer_amount = round(amount * providers.fx_rate(cur, "NGN"), 2)
        summary = {
            "order_id": oid,
            "refund_method": "bank_transfer",
            "reason": reason,
            "amount": _money(xfer_amount, "NGN"),
            "amount_value": xfer_amount,
            "currency": "NGN",
            "bank_code": bank_code,
            "account_number": account_number,
            "account_name": account_name,
            "transfer_reference": transfer_ref,
            "items": [{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in order.get("items") or []],
            "warning": "This pays out from the store's Paystack balance. Confirm the account name matches the customer.",
        }
        if args.get("confirm") is not True:
            return _ok(status="needs_confirmation", summary=summary,
                       instruction="Show the resolved account_name and amount. Call again with confirm=true only after the customer confirms both.")
        # Require the confirmed name to match (agent passes back account_name)
        confirmed_name = (args.get("account_name") or "").strip()
        if not confirmed_name:
            return _err("account_name is required to confirm a bank_transfer refund.",
                        resolved=account_name)
        if confirmed_name.lower() != account_name.lower():
            return _err("account_name does not match the resolved name.",
                        resolved=account_name, provided=confirmed_name)
        # Create recipient + initiate transfer
        recipient = payments.create_transfer_recipient(key, account_name, account_number, bank_code, "NGN")
        rcp = recipient.get("recipient_code")
        if not rcp:
            return _err("Transfer recipient creation failed", recipient=recipient)
        # Record pending transfer first for idempotency
        pre = store.save_transfer(transfer_ref, oid, xfer_amount, "NGN", "initiating",
                                  {"recipient_code": rcp, "account_name": account_name,
                                   "account_number": account_number, "bank_code": bank_code})
        if pre.get("status") in ("success", "pending", "otp") and pre.get("transfer_code"):
            return _err("Transfer already submitted for this order.", transfer=pre)
        try:
            tr = payments.initiate_transfer(key, xfer_amount, rcp, transfer_ref,
                                            reason="Shopmate refund %s: %s" % (oid, reason), currency="NGN")
        except Exception as e:
            store.update_transfer(transfer_ref, "failed", {"error": str(e)})
            return _err("Transfer initiate failed: %s" % e)
        store.update_transfer(transfer_ref, tr.get("status") or "pending", {
            "transfer_code": tr.get("transfer_code"), "provider": tr, "recipient_code": rcp,
            "account_name": account_name, "account_number": account_number, "bank_code": bank_code,
        })
        def mut(o):
            o["payment_status"] = "refund_pending"
            o["refund"] = {
                "status": "refund_pending", "reason": reason, "at": int(time.time()),
                "provider": "paystack_transfer", "transfer_reference": transfer_ref,
                "transfer_code": tr.get("transfer_code"), "amount": xfer_amount, "currency": "NGN",
                "account_name": account_name, "account_number": account_number, "bank_code": bank_code,
            }
            return o
        store.update_order(oid, mut)
        # Fetch latest status
        try:
            latest = payments.fetch_transfer(key, tr.get("transfer_code") or transfer_ref)
            store.update_transfer(transfer_ref, latest.get("status") or tr.get("status") or "pending", {"fetch": latest})
            if latest.get("status") == "success":
                def mut2(o):
                    o["payment_status"] = "refunded"
                    o["delivery_status"] = "cancelled"
                    r = dict(o.get("refund") or {})
                    r["status"] = "refunded"
                    r["transfer_status"] = "success"
                    o["refund"] = r
                    return o
                store.update_order(oid, mut2)
                return _ok(status="refunded", order_id=oid, amount=_money(xfer_amount, "NGN"),
                           account_name=account_name, transfer_reference=transfer_ref,
                           transfer_code=tr.get("transfer_code"),
                           note="Transfer succeeded. Order marked refunded.")
        except Exception:
            latest = tr
        return _ok(status="refund_pending", order_id=oid, amount=_money(xfer_amount, "NGN"),
                   account_name=account_name, transfer_reference=transfer_ref,
                   transfer_code=tr.get("transfer_code"), transfer_status=tr.get("status"),
                   note="Transfer queued. Webhooks (transfer.success/failed) or order_status refresh will finalize.")

    summary = {
        "order_id": oid,
        "amount": _money(amount, cur),
        "method": refund_method,
        "reason": reason,
        "items": [{"title": l["title"], "qty": l["qty"], "image": l.get("image")} for l in order.get("items") or []],
    }
    if refund_method == "crypto":
        payout = (args.get("payout_address") or "").strip()
        if not payout:
            return _err("Crypto refunds need payout_address (the wallet to receive the refund).", summary=summary)
        summary["payout_address"] = payout
        summary["crypto_coin"] = order.get("pay_coin") or order.get("crypto_coin")

    if args.get("confirm") is not True:
        return _ok(status="needs_confirmation", summary=summary,
                   instruction="Show this refund summary and call request_refund again with confirm=true only after the customer agrees.")

    if refund_method == "paystack":
        key = providers.get_secret("PAYSTACK_SECRET_KEY")
        if not key:
            return _err("Paystack refunds are not configured. No refund was submitted.", summary=summary)
        ref = order.get("payment_reference") or oid
        pay = payments.paystack_refund(key, ref, amount=amount, currency=cur, reason=reason)
        def mut(o):
            o["payment_status"] = "refunded"
            o["delivery_status"] = "cancelled"
            o["refund"] = {"status": "refunded", "reason": reason, "at": int(time.time()),
                           "provider": "paystack", "provider_ref": (pay or {}).get("id") or (pay or {}).get("transaction") or ref}
            return o
        order = store.update_order(oid, mut)
        return _ok(status="refunded", order_id=oid, amount=_money(amount, cur),
                   note="Paystack refund submitted. Funds return to the original payment method.")

    # crypto manual
    payout = (args.get("payout_address") or "").strip()
    def mut(o):
        o["payment_status"] = "refund_pending"
        o["refund"] = {"status": "refund_pending", "reason": reason, "at": int(time.time()),
                       "provider": "crypto_manual", "payout_address": payout,
                       "amount": amount, "currency": cur, "coin": o.get("pay_coin")}
        return o
    order = store.update_order(oid, mut)
    return _ok(status="refund_pending", order_id=oid, amount=_money(amount, cur),
               payout_address=payout,
               note="Crypto refund recorded for manual payout. The store operator will send funds to the address provided. Not auto-sent.")


@safe
def contact_support(args, **kwargs):
    """FAQ helper, open a support ticket, or look up ticket status."""
    action = (args.get("action") or "faq").strip().lower()
    cid = _cid(kwargs)

    support_email = providers.get_secret("SUPPORT_EMAIL") or providers.setting("support_email")
    support_wa = providers.get_secret("SUPPORT_WHATSAPP") or providers.setting("support_whatsapp")
    contact = {k: v for k, v in {"email": support_email, "whatsapp": support_wa}.items() if v}

    if action in ("faq", "help"):
        topic = (args.get("topic") or args.get("question") or "").strip().lower()
        faqs = FAQ
        if topic:
            faqs = [f for f in FAQ if topic in f["q"].lower() or topic in f["a"].lower()] or FAQ
        return _ok(faqs=faqs, operator_contact=contact or None,
                   note="Share the matching FAQ answer. For anything else, open a ticket with action=open.")

    if action in ("status", "list"):
        tid = (args.get("ticket_id") or "").strip()
        oid = (args.get("order_id") or "").strip()
        email = (args.get("email") or "").strip()
        tickets = store.find_tickets(ticket_id=tid or None, order_id=oid or None,
                                     email=email or None, conversation_id=None if (tid or oid or email) else cid)
        return _ok(tickets=[{k: t.get(k) for k in ("ticket_id", "order_id", "issue_type", "status", "created_at", "subject")}
                            for t in tickets], count=len(tickets), operator_contact=contact or None)

    if action != "open":
        return _err("action must be faq, open, status or list")

    issue = (args.get("issue_type") or "other").strip().lower()
    valid = {"missing", "damaged", "late", "wrong_item", "payment", "other", "refund"}
    if issue not in valid:
        return _err("issue_type must be one of: " + ", ".join(sorted(valid)))
    message = (args.get("message") or args.get("reason") or "").strip()
    if not message:
        return _err("message is required when opening a ticket")
    oid = (args.get("order_id") or "").strip()
    if oid and not store.get_order(oid):
        return _err("Order not found", order_id=oid)

    ticket_id = "TK-" + time.strftime("%y%m%d") + "-" + uuid.uuid4().hex[:6].upper()
    ticket = {
        "ticket_id": ticket_id,
        "order_id": oid,
        "conversation_id": cid,
        "issue_type": issue,
        "status": "open",
        "subject": (args.get("subject") or issue.replace("_", " ")).strip()[:120],
        "message": message[:2000],
        "email": (args.get("email") or "").strip(),
        "phone": (args.get("phone") or "").strip(),
        "created_at": int(time.time()),
    }
    store.save_ticket(ticket)
    return _ok(status="open", ticket_id=ticket_id, order_id=oid or None, issue_type=issue,
               operator_contact=contact or None,
               note="Ticket created. Tell the customer the ticket id"
                    + ((" and that they can reach the store at " + ", ".join("%s: %s" % (k, v) for k, v in contact.items()))
                       if contact else " — the store operator will follow up in this chat."))
