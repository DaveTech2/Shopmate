---
name: shopmate-shopping
description: How to run a shopping session with the shopmate tools — browse, search, cart, checkout, payment and delivery tracking.
---

# Shopping with shopmate

1. **Browse or search.** `shop_categories` for sections; `shop_search` with a query (and optional `category`, `max_price`).
   Show every result as a short card: `![title](image)`, **title**, price, merchant. If the note says *demo*, tell the
   user the prices are samples.
2. **Choose.** Only call `cart_add` for items the user picked. Confirm quantity. Use `cart_view` to recap.
3. **Delivery.** Ask for the city and use `delivery_quote`; offer standard vs express.
4. **Details.** Collect full name, email, phone, street address and city.
5. **Payment.** Use `payment_options`. Paystack takes local currencies (NGN, GHS, KES, ZAR, USD, XOF, EGP, RWF);
   crypto takes USDT, USDC, BTC, ETH, SOL and others.
6. **Checkout in two steps.** Call `checkout` without `confirm`, show the full summary (items, delivery fee, total,
   amount in the payment currency, exchange rate if any), and wait for an explicit "yes". Then call again with
   `confirm=true`.
7. **Pay.** Give the Paystack link, or for crypto the exact amount, coin/network and address (and memo if present).
   Warn that crypto must be sent on the right network and can't be reversed.
8. **Verify.** Never say "paid" until `order_status` returns `payment_status: paid`. Then share the delivery ETA.

Never invent prices, stock, or delivery promises the tools didn't return.
