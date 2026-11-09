"""Paystack webhook helpers.

Hermes does not host an HTTP endpoint for you. Operators should expose a small
HTTPS POST URL (Cloudflare Worker, Flask, nginx+cgi, etc.), call
`verify_and_handle`, and return 200 quickly.

Dashboard → Settings → API Keys & Webhooks → Webhook URL.

Verify with the classic `x-paystack-signature` header (HMAC-SHA512 of the raw
body using your Paystack secret key). Prefer the raw body bytes before JSON parsing.
"""

from __future__ import annotations

import json
from typing import Any

from . import payments, store


def verify_and_handle(raw_body: bytes | str, signature_header: str, secret_key: str | None = None) -> dict:
    """Verify signature then apply charge.success / transfer.* / refund.processed.

    Returns {"ok": True/False, ...}. On bad signature: ok=False, error='invalid signature'.
    """
    key = secret_key
    if not key:
        try:
            from .providers import get_secret
            key = get_secret("PAYSTACK_SECRET_KEY")
        except Exception:
            key = None
    if not key:
        return {"ok": False, "error": "PAYSTACK_SECRET_KEY not configured"}
    if not payments.verify_webhook_signature(key, raw_body, signature_header or ""):
        return {"ok": False, "error": "invalid signature"}
    try:
        body = raw_body if isinstance(raw_body, str) else raw_body.decode("utf-8")
        event = json.loads(body)
    except Exception as e:
        return {"ok": False, "error": "invalid json: %s" % e}
    result = payments.apply_webhook_event(
        event,
        get_order=store.get_order,
        update_order=store.update_order,
        save_card=store.save_card,
        update_transfer=store.update_transfer,
    )
    return {"ok": True, **result}


def handle_event(event: dict[str, Any]) -> dict:
    """Apply an already-verified event dict (for tests / trusted callers)."""
    return payments.apply_webhook_event(
        event,
        get_order=store.get_order,
        update_order=store.update_order,
        save_card=store.save_card,
        update_transfer=store.update_transfer,
    )
