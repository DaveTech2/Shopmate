# Shopmate 🛒 — your personal shopper agent for Hermes

Save the time and stress of shopping. Just tell the agent what you want:

1. **You say it** — "I need a Tecno Spark 30, under ₦200k, delivered to Lekki."
2. **It finds it** — live listings with real prices and photos.
3. **It shows you** the product, price, **delivery fee to your location** and the total delivered price, and asks
   "is this the one?"
4. **You say yes** — it takes payment (Paystack in your local currency, or crypto).
5. **It's delivered** to your address, and the agent tracks the order.

```
hermes plugins install shopmate
hermes plugins enable shopmate
```

## What it does

| Step | Tools |
|---|---|
| Locate the customer from a shared location pin, Google/Apple Maps link, or area name; every product then shows its doorstep fee and nearest-pickup option | `set_location`, `pickup_stops` |
| Browse 10 sections (groceries, phones, electronics, fashion, home & kitchen, beauty, baby, health, sports, automotive) | `shop_categories`, `shop_search` |
| Live product search — real prices, images, merchant names (Google Shopping) | `shop_search`, `shop_product` |
| Cart | `cart_add`, `cart_remove`, `cart_view`, `cart_clear` |
| Delivery fee + ETA by distance and item size, standard or express | `delivery_quote` |
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
| `SHOPMATE_ORIGIN` | Dispatch city delivery is calculated from (default `lagos`) |
| `SHOPMATE_COUNTRY` | Google Shopping country (default `ng`) |

Secrets are read only through Hermes' secret scope (`agent.secret_scope.get_secret`).

## Business model — read this

Shopmate is a **concierge store**: the customer pays *you* (the store operator) through Paystack or NOWPayments, and you
buy and deliver the items. Live search shows real third-party listings and prices; it does not place orders at those
merchants. ## Delivery fees

Shown on **every product** once the customer's location is known. Fee = distance band base + per-km charge (from
your dispatch city, using built-in coordinates for all 36 Nigerian states + FCT, major cities/areas, and key African
cities) + a size surcharge (small / medium / large / bulky, inferred from the product). Express = 1.8× and a day faster.
Edit `RATES` and `SIZE_SURCHARGE` in `delivery.py` to match your logistics partner.

**Doorstep or pickup.** When the customer shares a location pin or maps link, the fee is priced to their exact
coordinates. Shopmate also finds the closest pickup stops and offers collection there at 60% of the doorstep fee
(`PICKUP_DISCOUNT`). The bundled stops in `data/pickup_stops.json` are **samples** — put your real stops in
`pickup_stops.json` in the plugin's data directory (same format) and they replace the samples.

Location is never detected silently: the plugin has no access to the customer's device. It works from what the
customer shares in chat. Coordinates are matched to the nearest known place offline — no geocoding service is called.
With a live Paystack key, Shopmate refuses to charge for demo-catalog items.

## Disclosures (catalog rule 13)

- **Third-party network calls**: `serpapi.com` (search, only when the key is set), `api.paystack.co` (create and verify
  payments), `api.nowpayments.io` (create and check crypto payments), `open.er-api.com` (exchange rates, no key; only
  when converting currencies).
- **Data sent**: the search query to SerpAPI; customer email, amount, currency, order id, item count and city to
  Paystack; amount in USD, coin, order id and a short description to NOWPayments.
- **Local storage**: carts, saved delivery locations (coordinates if shared), orders (including customer name, email,
  phone and address) and cached search results live
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
