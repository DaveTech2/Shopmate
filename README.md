# Shopmate 🛒 — a shopping agent for Hermes

Turn Hermes into a full shopping assistant: browse product sections, search **live prices with photos**, build a
cart, get a delivery quote, and pay with **Paystack in local currencies** or with **crypto** — all in one chat.

```
hermes plugins install shopmate
hermes plugins enable shopmate
```

## What it does

| Step | Tools |
|---|---|
| Browse 10 sections (groceries, phones, electronics, fashion, home & kitchen, beauty, baby, health, sports, automotive) | `shop_categories`, `shop_search` |
| Live product search — real prices, images, merchant names (Google Shopping) | `shop_search`, `shop_product` |
| Cart | `cart_add`, `cart_remove`, `cart_view`, `cart_clear` |
| Delivery fee + ETA by city, standard or express | `delivery_quote` |
| Pay: Paystack (NGN, GHS, KES, ZAR, USD, XOF, EGP, RWF) or crypto (USDT, USDC, BTC, ETH, SOL, BNB, TRX, LTC) | `payment_options`, `checkout` |
| Verify payment and track the order | `order_status` |

A bundled skill (`shopmate:shopmate-shopping`) tells the agent how to run the flow: two-step checkout with explicit
user confirmation, and never claiming an order is paid until the provider confirms it.

## Configuration

All keys are optional. With none set, Shopmate runs on its bundled **demo catalog (sample prices, clearly labelled)**
and refuses to take payment.

| Secret | Enables |
|---|---|
| `SERPAPI_API_KEY` | Live Google Shopping results ([serpapi.com](https://serpapi.com)) |
| `PAYSTACK_SECRET_KEY` | Paystack checkout links. Use `sk_test_…` while testing. Only currencies enabled on your Paystack business account will work. |
| `NOWPAYMENTS_API_KEY` | Crypto checkout ([nowpayments.io](https://nowpayments.io)) |
| `SHOPMATE_COUNTRY` | Google Shopping country (default `ng`) |

Secrets are read only through Hermes' secret scope (`agent.secret_scope.get_secret`).

## Business model — read this

Shopmate is a **concierge store**: the customer pays *you* (the store operator) through Paystack or NOWPayments, and you
buy and deliver the items. Live search shows real third-party listings and prices; it does not place orders at those
merchants. Delivery fees and ETAs are the zone table in `tools.py` — edit them to match your logistics partner.
With a live Paystack key, Shopmate refuses to charge for demo-catalog items.

## Disclosures (catalog rule 13)

- **Third-party network calls**: `serpapi.com` (search, only when the key is set), `api.paystack.co` (create and verify
  payments), `api.nowpayments.io` (create and check crypto payments), `open.er-api.com` (exchange rates, no key; only
  when converting currencies).
- **Data sent**: the search query to SerpAPI; customer email, amount, currency, order id, item count and city to
  Paystack; amount in USD, coin, order id and a short description to NOWPayments.
- **Local storage**: carts, orders (including customer name, email, phone and address) and cached search results live
  in `state.json` in the plugin's data directory — never in the plugin tree.
- **No** shell commands, background processes, reads outside the plugin's data, stored credentials or telemetry.
- Payments need explicit user confirmation (`checkout` refuses to create a payment without `confirm=true`).

## Development

```
python3 -I shopmate/tests/test_plugin.py    # offline end-to-end test (stubs Hermes + HTTP)
hermes plugins validate ./shopmate --install-deps
```

No third-party Python dependencies (stdlib only, plus PyYAML in the test).

## License

MIT
