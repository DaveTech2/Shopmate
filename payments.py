"""Payment rails.

- Paystack: local currencies (NGN, GHS, KES, ZAR, USD, XOF, EGP, RWF — whichever
  your Paystack business account has enabled). The customer pays on Paystack's
  hosted page; card data never touches the plugin.
- NOWPayments: crypto (USDT, USDC, BTC, ETH, SOL, …). The customer sends the
  exact amount to a one-time deposit address; status is checked via the API.
- Refunds: Paystack POST /refund; crypto is recorded as a manual payout request.
"""

import json
import urllib.error

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
    data = _call(PAYSTACK + "/transaction/initialize", {"Authorization": "Bearer " + key}, body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack initialize failed")
    return data["data"]


def paystack_verify(key, reference):
    data = _call(PAYSTACK + "/transaction/verify/" + reference, {"Authorization": "Bearer " + key})
    return data.get("data") or {}


def paystack_refund(key, reference, amount=None, currency=None, reason=None):
    """Refund a Paystack charge by transaction reference (order id). Amount in major units."""
    body = {"transaction": reference}
    if amount is not None:
        body["amount"] = int(round(float(amount) * 100))
    if currency:
        body["currency"] = currency
    if reason:
        body["customer_note"] = str(reason)[:500]
        body["merchant_note"] = str(reason)[:500]
    data = _call(PAYSTACK + "/refund", {"Authorization": "Bearer " + key}, body, "POST")
    if not data.get("status"):
        raise RuntimeError(data.get("message") or "Paystack refund failed")
    return data.get("data") or data


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
