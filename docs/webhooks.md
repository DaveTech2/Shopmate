# Wiring Paystack webhooks for Shopmate

Hermes does not expose an HTTP endpoint. Run a tiny HTTPS receiver yourself and
point Paystack Dashboard → Settings → API Keys & Webhooks at it.

```python
from shopmate.webhooks import verify_and_handle

def handler(request):
    # Use the RAW body bytes (before JSON parse)
    raw = request.body
    sig = request.headers.get("x-paystack-signature", "")
    result = verify_and_handle(raw, sig)  # uses PAYSTACK_SECRET_KEY
    if not result.get("ok"):
        return Response(status=401)
    return Response(status=200)  # acknowledge immediately
```

Handled events: `charge.success`, `transfer.success`, `transfer.failed`,
`transfer.reversed`, `refund.processed`.

Signature: HMAC-SHA512 of the raw body with your Paystack **secret key**, hex
digest in `x-paystack-signature` (constant-time compare).
