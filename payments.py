"""Payment rails (Paystack + NOWPayments).

Paystack:
- Initialize / Verify / Charge Authorization (saved cards)
- Refunds (POST /refund)
- Transfers (opt-in): resolve account, create recipient, initiate, fetch
- Card BIN resolution (optional card metadata)
- Webhook signature verification (x-paystack-signature HMAC-SHA512)

BVN Resolution/Match and Phone (Truecaller) resolution are intentionally NOT used.
"""

import hashlib
import hmac
import json
import urllib.error
import urllib.parse

from .providers import _http_json

PAYSTACK = "https://api.paystack.co"
NOWPAYMENTS = "https://api.nowpayments.io/v1"

PAYSTACK_CURRENCIES = {"NGN", "GHS", "KES", "ZAR", "USD", "XOF", "EGP", "RWF"}
PAYSTACK_CHANNELS = {
    "NGN": ["card", "bank", "ussd", "bank_transfer", "qr"],
    "GHS": ["card", "mobile_money", "bank"],
    "KES": ["card", "mobile_money"],
    "ZAR": ["card", "eft"],
    "XOF": ["card", "mobile_money"],
}
CRYPTO_COINS = {
    "usdt": "usdttrc20", "usdt-trc20": "usdttrc20", "usdt-bsc": "usdtbsc", "usdt-erc20": "usdterc20",
    "usdc": "usdcsol", "usdc-sol": "usdcsol", "usdc-base": "usdcbase", "usdc-erc20": "usdc",
    "btc": "btc", "eth": "eth", "sol": "sol", "bnb": "bnbbsc", "trx": "trx", "ltc": "ltc",
}


def _call(url, key_headers, body=None, method=None):
    data = json.dumps(body).encode() if body is not None else None
    try:
        return _http_json(url, data=data, headers=dict(key_headers, **{"Content-Type": "application/json"}), method=method)
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s: %s" % (e.code, e.read().decode("utf-8", "replace")[:300]))


def _auth(key):
    return {"Authorization": "Bearer " + key}


# ---------- safe authorization snapshot (never full PAN) ----------

def sanitize_authorization(auth: dict) -> dict | None:
    """Keep only reusable-card metadata safe to store. Never stores full card numbers."""
    if not auth or not isinstance(auth, dict):
        return None
    code = auth.get("authorization_code")
    if not code:
        return None
    return {
        "authorization_code": code,
        "reusable": bool(auth.get("reusable")),
        "signature": auth.get("signature"),
        "bin": auth.get("bin"),
        "last4": auth.get("last4"),
        "exp_month": auth.get("exp_month"),
        "exp_year": auth.get("exp_year"),
        "card_type": (auth.get("card_type") or "").strip() or None,
        "bank": auth.get("bank"),
        "brand": (auth.get("brand") or "").strip() or None,
        "channel": auth.get("channel"),
        "country_code": auth.get("country_code"),
    }


# ---------- Transactions ----------

def paystack_initialize(key, email, amount, currency, reference, metadata):
    body = {
        "email": email,
        "amount": int(round(amount * 100)),
        "currency": currency,
        "reference": reference,
        "metadata": metadata,
    }
    if currency in PAYSTACK_CHANNELS:
        body["channels"] = PAYSTACK_CHANNELS[currency]
    data = _call(PAYSTACK + "/transaction/initialize", _auth(key), body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack initialize failed")
    return data["data"]


def paystack_verify(key, reference):
    """GET /transaction/verify/:reference — returns the transaction data object."""
    data = _call(PAYSTACK + "/transaction/verify/" + urllib.parse.quote(str(reference), safe=""), _auth(key))
    return data.get("data") or {}


def paystack_charge_authorization(key, email, amount, currency, authorization_code, reference, metadata=None):
    """POST /transaction/charge_authorization — charge a reusable saved card."""
    body = {
        "email": email,
        "amount": int(round(float(amount) * 100)),
        "currency": currency,
        "authorization_code": authorization_code,
        "reference": reference,
    }
    if metadata is not None:
        body["metadata"] = metadata
    data = _call(PAYSTACK + "/transaction/charge_authorization", _auth(key), body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack charge_authorization failed")
    return data.get("data") or {}


def paystack_refund(key, reference, amount=None, currency=None, reason=None):
    body = {"transaction": reference}
    if amount is not None:
        body["amount"] = int(round(float(amount) * 100))
    if currency:
        body["currency"] = currency
    if reason:
        body["customer_note"] = str(reason)[:500]
        body["merchant_note"] = str(reason)[:500]
    data = _call(PAYSTACK + "/refund", _auth(key), body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack refund failed")
    return data.get("data") or data


# ---------- Banks / identity (account resolve + BIN only) ----------

def list_banks(key, country="nigeria", currency="NGN"):
    """GET /bank — Nigerian bank list for transfer recipient setup."""
    qs = urllib.parse.urlencode({"country": country, "currency": currency})
    data = _call(PAYSTACK + "/bank?" + qs, _auth(key))
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "List banks failed")
    return data.get("data") or []


def resolve_account(key, account_number, bank_code):
    """GET /bank/resolve — Account Number Resolution (shows account_name for confirm)."""
    qs = urllib.parse.urlencode({"account_number": account_number, "bank_code": bank_code})
    data = _call(PAYSTACK + "/bank/resolve?" + qs, _auth(key))
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Account resolve failed")
    return data.get("data") or {}


def resolve_card_bin(key, bin6):
    """GET /decision/bin/:bin — card brand/bank metadata only (no BVN/phone)."""
    bin6 = "".join(c for c in str(bin6 or "") if c.isdigit())[:6]
    if len(bin6) < 6:
        raise RuntimeError("BIN must be at least 6 digits")
    data = _call(PAYSTACK + "/decision/bin/" + bin6, _auth(key))
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "BIN resolve failed")
    return data.get("data") or {}


# ---------- Transfers ----------

def create_transfer_recipient(key, name, account_number, bank_code, currency="NGN"):
    """POST /transferrecipient — type nuban for Nigerian accounts."""
    data = _call(PAYSTACK + "/transferrecipient", _auth(key), {
        "type": "nuban",
        "name": name,
        "account_number": account_number,
        "bank_code": bank_code,
        "currency": currency,
    }, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Create transfer recipient failed")
    return data.get("data") or {}


def initiate_transfer(key, amount, recipient_code, reference, reason=None, currency="NGN"):
    """POST /transfer — amount in major units; converted to kobo/pesewas."""
    body = {
        "source": "balance",
        "amount": int(round(float(amount) * 100)),
        "recipient": recipient_code,
        "reference": reference,
        "currency": currency,
    }
    if reason:
        body["reason"] = str(reason)[:200]
    data = _call(PAYSTACK + "/transfer", _auth(key), body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Initiate transfer failed")
    return data.get("data") or {}


def fetch_transfer(key, id_or_code):
    """GET /transfer/:id_or_code"""
    data = _call(PAYSTACK + "/transfer/" + urllib.parse.quote(str(id_or_code), safe=""), _auth(key))
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Fetch transfer failed")
    return data.get("data") or {}


def verify_transfer(key, reference):
    """GET /transfer/verify/:reference"""
    data = _call(PAYSTACK + "/transfer/verify/" + urllib.parse.quote(str(reference), safe=""), _auth(key))
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Verify transfer failed")
    return data.get("data") or {}


# ---------- Webhooks ----------

def verify_webhook_signature(secret_key: str, raw_body: bytes | str, signature_header: str) -> bool:
    """Verify classic x-paystack-signature: HMAC-SHA512(raw body, secret key) as lowercase hex.

    Prefer verifying against the raw request body bytes before JSON parsing.
    Also accepts the newer Paystack-Signature header format when present as a
    simple hex digest equal to HMAC of the body (legacy path). The whsec_
    timestamped scheme should be verified by the operator's HTTP layer if used;
    this helper covers the documented x-paystack-signature secret-key HMAC.
    """
    if not secret_key or signature_header is None:
        return False
    body = raw_body if isinstance(raw_body, (bytes, bytearray)) else str(raw_body).encode("utf-8")
    sig = str(signature_header).strip().lower()
    # Newer header can look like: t=…,v1=whk_…:hex — try each hex part; also plain hex.
    candidates = []
    if "," in sig or "v1=" in sig:
        for part in sig.split(","):
            part = part.strip()
            if part.startswith("v1="):
                val = part[3:]
                if ":" in val:
                    val = val.split(":", 1)[1]
                candidates.append(val.strip())
    else:
        candidates.append(sig)
    expected = hmac.new(secret_key.encode("utf-8"), body, hashlib.sha512).hexdigest()
    for c in candidates:
        if len(c) == 128 and hmac.compare_digest(expected, c):
            return True
    return False


def apply_webhook_event(event: dict, *, get_order, update_order, save_card=None, update_transfer=None) -> dict:
    """Apply a verified Paystack webhook event to local order/transfer state.

    Handles: charge.success, transfer.success/failed/reversed, refund.processed.
    Returns a small result dict for logging. Caller must verify the signature first.
    """
    name = (event or {}).get("event") or ""
    data = (event or {}).get("data") or {}
    out = {"event": name, "handled": False}

    if name == "charge.success":
        ref = data.get("reference")
        if not ref:
            return dict(out, error="missing reference")
        order = get_order(ref)
        if not order:
            return dict(out, error="order not found", reference=ref)
        amount_ok = int(data.get("amount") or 0) == int(round(float(order.get("charge") or 0) * 100))
        currency_ok = (data.get("currency") or "").upper() == (order.get("pay_currency") or "").upper()
        if data.get("status") == "success" and amount_ok and currency_ok:
            auth = sanitize_authorization(data.get("authorization") or {})
            def mut(o):
                if o.get("payment_status") != "paid":
                    o["payment_status"] = "paid"
                    o["delivery_status"] = "processing"
                    o["paid_at"] = o.get("paid_at") or int(__import__("time").time())
                o["paystack_transaction_id"] = data.get("id")
                if auth:
                    o["authorization"] = auth
                return o
            update_order(ref, mut)
            if auth and auth.get("reusable") and save_card:
                email = ((order.get("customer") or {}).get("email") or (data.get("customer") or {}).get("email") or "")
                if email:
                    save_card(email, auth)
            out.update(handled=True, order_id=ref, payment_status="paid")
        else:
            out.update(error="amount/currency/status mismatch", reference=ref)
        return out

    if name in ("transfer.success", "transfer.failed", "transfer.reversed"):
        ref = data.get("reference")
        code = data.get("transfer_code") or data.get("id")
        status_map = {
            "transfer.success": "success",
            "transfer.failed": "failed",
            "transfer.reversed": "reversed",
        }
        st = status_map[name]
        if update_transfer and ref:
            update_transfer(ref, st, data)
        # If transfer is tied to an order refund, update order payment_status.
        order_id = None
        if ref and str(ref).startswith("SM-TRF-"):
            order_id = str(ref)[len("SM-TRF-"):].split("~")[0]
        elif data.get("reason") and "SM-" in str(data.get("reason")):
            pass
        if order_id and get_order(order_id):
            def mut(o):
                refund = dict(o.get("refund") or {})
                refund["transfer_status"] = st
                refund["transfer_code"] = code
                refund["transfer_reference"] = ref
                if st == "success":
                    o["payment_status"] = "refunded"
                    o["delivery_status"] = "cancelled"
                    refund["status"] = "refunded"
                elif st == "failed":
                    refund["status"] = "transfer_failed"
                    if o.get("payment_status") == "refund_pending":
                        pass
                elif st == "reversed":
                    refund["status"] = "transfer_reversed"
                    o["payment_status"] = "paid"  # money came back
                o["refund"] = refund
                return o
            update_order(order_id, mut)
            out.update(order_id=order_id)
        out.update(handled=True, transfer_reference=ref, transfer_status=st)
        return out

    if name == "refund.processed":
        # Paystack card refund finished.
        tx = data.get("transaction") or {}
        ref = tx.get("reference") if isinstance(tx, dict) else None
        ref = ref or data.get("transaction_reference") or data.get("reference")
        if ref and get_order(ref):
            def mut(o):
                o["payment_status"] = "refunded"
                o["delivery_status"] = "cancelled"
                refund = dict(o.get("refund") or {})
                refund["status"] = "refunded"
                refund["provider"] = "paystack"
                o["refund"] = refund
                return o
            update_order(ref, mut)
            out.update(handled=True, order_id=ref, payment_status="refunded")
        else:
            out.update(error="order not found for refund", reference=ref)
        return out

    return dict(out, note="ignored event")


# ---------- NOWPayments ----------

def crypto_create(key, amount_usd, coin, order_id, description):
    data = _call(NOWPAYMENTS + "/payment", {"x-api-key": key}, {
        "price_amount": round(amount_usd, 2),
        "price_currency": "usd",
        "pay_currency": coin,
        "order_id": order_id,
        "order_description": description[:200],
    }, "POST")
    if not data.get("payment_id"):
        raise RuntimeError(data.get("message") or "NOWPayments create failed")
    return data


def crypto_status(key, payment_id):
    return _call(NOWPAYMENTS + "/payment/" + str(payment_id), {"x-api-key": key})
