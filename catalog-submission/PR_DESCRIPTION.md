## Add shopmate to the plugin catalog

**What it does:** A shopping agent. Users browse 10 product sections, search live prices with photos (Google Shopping
via SerpAPI), build a cart, get a delivery quote, and pay with Paystack (NGN, GHS, KES, ZAR, USD, XOF, EGP, RWF) or
crypto via NOWPayments. Payment is verified with the provider before an order is marked paid.

**Hermes surfaces used:** `ctx.register_tool` (11 tools, toolset `shopmate`) and `ctx.register_skill` (1 bundled skill).
No hooks, no overrides, no CLI commands. Secrets come from `agent.secret_scope.get_secret`.

**Rule 13 disclosures**
- Third-party network calls: serpapi.com, api.paystack.co, api.nowpayments.io, open.er-api.com (details in README).
- Stores carts and orders (including customer contact and delivery details) in the plugin data dir.
- No shell commands, background processes, reads outside plugin data, stored credentials or telemetry.
- `checkout` requires an explicit `confirm=true` after the user sees the summary; no auto-approval.
