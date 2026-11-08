# Shopmate 🛒 — your personal shopper agent for Hermes

Save the time and stress of shopping. Just tell the agent what you want:

Works in **every country and city** — prices, merchants, delivery fees and payment in the customer's local currency.

1. **You say it** — "I need a Tecno Spark 30, under ₦200k, delivered to Lekki." (or "AirPods to Brooklyn", "a kettle to Westminster")
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

A bundled skill (`shopmate:shopping`) tells the agent how to run the flow: two-step checkout with explicit
user confirmation, and never claiming an order is paid until the provider confirms it.

## Configuration

Everything is optional. With no keys, Shopmate runs on its bundled **demo catalog (sample prices, clearly
labelled)** and refuses to take payment. All of it can also be set from the Desktop app's **Settings → Plugins**.

**Secrets** (`.env`, read only through Hermes' secret scope):

| Variable | Enables |
|---|---|
| `SERPAPI_API_KEY` | Live Google Shopping results ([serpapi.com](https://serpapi.com)) |
| `PAYSTACK_SECRET_KEY` | Paystack checkout links. Use `sk_test_…` while testing. Only currencies enabled on your Paystack business account will work. |
| `NOWPAYMENTS_API_KEY` | Crypto checkout ([nowpayments.io](https://nowpayments.io)) |

**Settings** (`config.yaml`, under `plugins.entries.shopmate.settings`):

| Key | Default | Meaning |
|---|---|---|
| `origin` | `lagos` | City or address you dispatch from; delivery is priced from here |
| `abroad` | `local` | Customers outside your home country: `local` = merchants in their own city, `ship` = ship internationally |
| `fallback_country` | `ng` | Google Shopping country used when the customer's country has no results |

## Business model — read this

Shopmate is a **concierge store**: the customer pays *you* (the store operator) through Paystack or NOWPayments, and you
buy and deliver the items. Live search shows real third-party listings and prices; it does not place orders at those
merchants. ## Delivery fees

Shown on **every product** once the customer's location is known — anywhere in the world. Places in the bundled
list resolve instantly offline; any other city, town, landmark or full street address is looked up on
OpenStreetMap (Nominatim). Live search follows the customer's country, so they see local merchants in their local
currency (falling back to `fallback_country` where Google Shopping isn't available).

Customers in your home country ship from `origin`. Customers abroad are served by merchants in their own
city (`abroad: local`, default), or set `abroad: ship` to ship internationally from your origin.
Nigerian dispatch uses naira rate tables; everywhere else uses USD tables converted to the local currency.
 Fee = distance band base + per-km charge (from
your dispatch city, using built-in coordinates for all 36 Nigerian states + FCT, major cities/areas, and key African
cities) + a size surcharge (small / medium / large / bulky, inferred from the product). Express = 1.8× and a day faster.
Edit `RATES` and `SIZE_SURCHARGE` in `delivery.py` to match your logistics partner.

**Doorstep or pickup.** When the customer shares a location pin or maps link, the fee is priced to their exact
coordinates. Shopmate also finds the closest pickup stops and offers collection there at 60% of the doorstep fee
(`PICKUP_DISCOUNT`). The bundled stops in `data/pickup_stops.json` are **samples** — put your real stops in
`pickup_stops.json` in the plugin's data directory (same format) and they replace the samples.

Location is never detected silently: the plugin has no access to the customer's device. It works from what the
customer shares in chat. Coordinates near a bundled place are named offline; elsewhere they are named via OpenStreetMap.
With a live Paystack key, Shopmate refuses to charge for demo-catalog items.

## Disclosures (catalog rule 13)

- **Third-party network calls**: `serpapi.com` (search, only when the key is set), `api.paystack.co` (create and verify
  payments), `api.nowpayments.io` (create and check crypto payments), `open.er-api.com` (exchange rates, no key; only
  when converting currencies), `nominatim.openstreetmap.org` (place lookup, only for places not in the bundled list;
  max 1 request/second, identifying User-Agent, results cached).
- **Data sent**: the place name/address or coordinates to OpenStreetMap; the search query and country to SerpAPI; customer email, amount, currency, order id, item count and city to
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
