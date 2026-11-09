# Shopmate 🛒 — your personal shopper agent for Hermes

Save the time and stress of shopping. Just tell the agent what you want:

Works in **every country and city** — prices, merchants, delivery fees and payment in the customer's local currency.

1. **You say it** — "I need toothpaste" or "AirPods to Brooklyn".
2. **It shows brand options** — real photos, prices and delivery for several brands (e.g. Close-Up, Colgate, Sensodyne), and asks which one you want.
3. **You pick** — it adds that exact item to the cart (with the real product image).
4. **You say yes** — it takes payment (Paystack in your local currency, or crypto).
5. **It's delivered** to your address, and the agent tracks the order. Refunds and support tickets are built in.

```
hermes plugins install shopmate
hermes plugins enable shopmate
```

Or from Git: `hermes plugins install DaveTech2/Shopmate --enable`

## What it does

| Step | Tools |
|---|---|
| Locate the customer from a shared location pin, Google/Apple Maps link, or area name | `set_location`, `pickup_stops` |
| Browse 24 sections — or ask for anything; several brand options with **real product photos** before cart | `shop_categories`, `shop_search`, `shop_product` |
| Cart and bulk orders — images kept on every line | `cart_add`, `cart_update`, `cart_remove`, `cart_view`, `cart_clear` |
| Delivery fee + ETA by distance and item size | `delivery_quote` |
| Pay: Paystack or crypto (USDT, USDC, BTC, ETH, SOL, …) | `payment_options`, `checkout` |
| Instant order lookup + Paystack verify (amount/currency match) | `order_status` |
| Saved reusable cards (charge authorization) | `saved_cards`, `checkout` (`saved_card`) |
| Refunds (Paystack Refund API, optional NGN bank transfer, or crypto) | `request_refund` |
| FAQ + support tickets | `contact_support` |

A bundled skill (`shopmate:shopping`) tells the agent how to run the flow: ask which brand when several match, always show the exact product image, two-step checkout/refund with explicit confirmation, and never claim paid until the provider confirms.

## Configuration

Everything is optional. With no keys, Shopmate runs on its bundled **demo catalog (sample prices, real packshot photos)** and refuses to take payment. All of it can also be set from the Desktop app's **Settings → Plugins**.

**Secrets** (`.env`, read only through Hermes' secret scope):

| Variable | Enables |
|---|---|
| `SERPAPI_API_KEY` | Live Google Shopping results ([serpapi.com](https://serpapi.com)) |
| `PAYSTACK_SECRET_KEY` | Paystack checkout + refunds. Use `sk_test_…` while testing. |
| `NOWPAYMENTS_API_KEY` | Crypto checkout ([nowpayments.io](https://nowpayments.io)) |
| `SUPPORT_EMAIL` | Optional operator email shown on support tickets |
| `SUPPORT_WHATSAPP` | Optional operator WhatsApp shown on support tickets |
| `SHOPMATE_ALLOW_TRANSFERS` | Set `1` to allow NGN bank payouts via Paystack Transfers (off by default; moves operator funds) |

**Settings** (`config.yaml`, under `plugins.entries.shopmate.settings`):

| Key | Default | Meaning |
|---|---|---|
| `origin` | `lagos` | City or address you dispatch from |
| `abroad` | `local` | Customers abroad: `local` = local merchants, `ship` = ship from origin |
| `auto_locate` | `true` | Detect city from the network when no location shared |
| `fallback_country` | `ng` | Google Shopping country when the customer's country has no results |
| `support_email` / `support_whatsapp` | _(empty)_ | Shown on tickets when secrets are unset |

## Business model — read this

Shopmate is a **concierge store**: the customer pays *you* (the store operator) through Paystack or NOWPayments, and you buy and deliver the items. Live search shows real third-party listings and prices; it does not place orders at those merchants.

## Product images

Every product the customer sees — search, cart, checkout summary and order history — carries the **exact product photo** for that listing:

- Live Google Shopping / SerpAPI results pass through the listing thumbnail; listings without a photo are dropped.
- The demo catalog only includes SKUs with a verified real packshot URL (Open Beauty/Food Facts or Wikimedia). Placeholders are not used.

## Fast order lookup

Orders and support tickets are stored in `orders.sqlite` under the plugin data directory, indexed by order id, email, phone and conversation. `order_status` answers in milliseconds even with thousands of orders, and skips the payment-provider API unless you pass `refresh=true`.

## Refunds & support

- `request_refund`: paid + not delivered + within 14 days. Preview without `confirm`, then `confirm=true`. Paystack → `POST /refund`. Crypto → `refund_pending` with the customer's `payout_address` (manual; never auto-sent). Double-refunds are refused.
- `contact_support`: FAQ (delivery, payments, refund policy), open a ticket (`missing` / `damaged` / `late` / `wrong_item` / `payment` / `other`), or check ticket status.

## Paystack deep integration (v0.5)

- **Verify**: after checkout, `order_status` (with `refresh=true` or a pending order id) calls `GET /transaction/verify/:reference` and marks the order **paid only** when status is `success` and amount+currency match. Reusable `authorization` metadata (code, last4, brand, bank, BIN — never full PAN) is stored and offered as a saved card.
- **Charge authorization**: returning customers can pay with `payment_method=saved_card` + `card_id` (`POST /transaction/charge_authorization`), still behind `confirm=true`. `saved_cards` lists/removes cards; optional Card BIN lookup enriches brand/bank.
- **Transfers (opt-in)**: with `SHOPMATE_ALLOW_TRANSFERS=1`, `request_refund` `refund_method=bank_transfer` resolves the NUBAN (`GET /bank/resolve` + `GET /bank`), shows the **account name** for confirmation, creates a recipient, initiates `POST /transfer` with idempotent reference `SM-TRF-{order_id}`, caps amount at the paid total, and fetches status. Disabled by default because it moves the operator's Paystack balance.
- **Webhooks**: `shopmate.webhooks.verify_and_handle(raw_body, x-paystack-signature)` verifies HMAC-SHA512 with the secret key and applies `charge.success`, `transfer.success|failed|reversed`, `refund.processed`. Hermes does not host HTTP — wire a small HTTPS endpoint yourself (see `webhooks.py`).
- **Not used**: BVN Resolution/Match and Phone (Truecaller) resolution — sensitive identity APIs with no role in shopping checkout.

## Disclosures (catalog rule 13)

- **Third-party network calls**: `serpapi.com`, `api.paystack.co` (initialize, verify, charge_authorization, refund, bank list/resolve, transferrecipient, transfer, decision/bin, webhooks), `api.nowpayments.io`, `open.er-api.com`, `ipapi.co`, `nominatim.openstreetmap.org`.
- **Data sent**: place name/coords to OpenStreetMap; search query to SerpAPI; customer email/amount/order id/card authorization code (never PAN) to Paystack; NUBAN + bank code when bank-transfer refunds are enabled; amount/coin/order id to NOWPayments.
- **Local storage**: carts and locations in `state.json`; orders, tickets, saved-card authorizations (last4/brand/bank + authorization_code) and transfer records in `orders.sqlite` — never in the plugin tree.
- **No** shell commands, background processes, reads outside the plugin's data, or BVN/phone identity lookups.
- Payments, saved-card charges, refunds and bank transfers need explicit user confirmation (`confirm=true`). Transfers also need `SHOPMATE_ALLOW_TRANSFERS=1`.

## Changelog

### 0.5.0

- Paystack verify before marking paid (amount/currency match); store reusable authorizations
- Saved-card checkout via charge_authorization; `saved_cards` list/remove + optional BIN lookup
- Opt-in NGN bank-transfer refunds (resolve account → confirm name → transfer with idempotent ref)
- Webhook helper (`webhooks.verify_and_handle`) for charge.success / transfer.* / refund.processed
- Explicitly skips BVN and phone identity APIs

### 0.4.2

- Indexed SQLite order/ticket store for instant lookup by id, email, phone or conversation
- Real verified product images on every demo SKU; live results require a thumbnail
- Brand options: toothpaste / body wash and similar queries return several brands and ask which one before cart
- `request_refund` (Paystack refund API + manual crypto payout) and `contact_support` (FAQ + tickets)
- Fixes: `fee_currency` after FX; demo search synonyms (soap/bar, paper/roll); body-wash SKUs; pet-shampoo ranking

### 0.4.1

- Pro Max demo SKU, tighter search, safer checkout, maps short-links

## Development

```
python3 -I shopmate/tests/test_plugin.py    # offline end-to-end test (stubs Hermes + HTTP)
hermes plugins validate ./shopmate --install-deps
```

No third-party Python dependencies (stdlib only, plus PyYAML in the test).

## License

MIT
