"""Tool schemas — what the model reads."""

_PID = {"type": "string", "description": "Product id from shop_search results (e.g. 'd-toi1' or 'g-…')"}

SHOP_CATEGORIES = {
    "name": "shop_categories",
    "description": "List the store's product sections (groceries, phones, electronics, fashion, home & kitchen, beauty, baby, health, sports, automotive). Call this when the user wants to browse.",
    "parameters": {"type": "object", "properties": {}},
}

SHOP_SEARCH = {
    "name": "shop_search",
    "description": (
        "Search products. With a search term and live search configured, returns real current prices, photos and "
        "merchants from Google Shopping; otherwise returns the demo catalog (sample prices — say so). "
        "Follow the shopping playbook: skill_view('shopmate:shopping'). "
        "Show each result with its image as markdown ![title](image), price and merchant. "
        "When ask_which/brand_options is set, present several brands with photos and ASK which one "
        "(and size/variant) before cart_add. "
        "Every result carries delivery_display and total_delivered for the customer's saved location — always show "
        "them. best_match is the top pick to present for confirmation. To browse a section, pass category with an empty query."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What the user wants, e.g. 'toothpaste' or 'Tecno Spark 30'"},
            "category": {"type": "string", "description": "Optional section id from shop_categories"},
            "max_price": {"type": "number", "description": "Optional budget ceiling in the product currency"},
            "limit": {"type": "integer", "description": "Max results (1-20, default 8)"},
        },
    },
}

SET_LOCATION = {
    "name": "set_location",
    "description": (
        "Locate the customer for delivery. Call with NO arguments to use the customer's current location "
        "(detected automatically from the network, city-level). Call this as soon as the customer mentions where they are (e.g. 'I'm in London') — don't ask again. Best: their shared location pin or Google Maps link (exact doorstep "
        "coordinates) via lat/lon or shared_location; otherwise a city/area name. Do this early: once set, every "
        "product shows the doorstep delivery fee, the nearest pickup stop and the total price. Returns the nearest pickup stops."
    ),
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string", "description": "Any city, town, area or full street address worldwide, e.g. 'Lekki', 'Brooklyn, New York', '10 Downing St, London'"},
        "state": {"type": "string", "description": "Optional state/region, e.g. 'Lagos', 'Ontario', 'Greater London'"},
        "country": {"type": "string", "description": "Optional ISO country code to disambiguate, e.g. 'NG', 'GB', 'US', 'IN'"},
        "lat": {"type": "number", "description": "Latitude from a shared location pin"},
        "lon": {"type": "number", "description": "Longitude from a shared location pin"},
        "shared_location": {"type": "string", "description": "A shared location / Google or Apple Maps link / 'lat,lon' text, pasted as-is"},
    }},
}

PICKUP_STOPS = {
    "name": "pickup_stops",
    "description": "List the pickup stops closest to the customer (saved location, or a given place), with distance, hours and a map link.",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string"},
        "lat": {"type": "number", "description": "Latitude from a shared location pin"},
        "lon": {"type": "number", "description": "Longitude from a shared location pin"},
        "shared_location": {"type": "string", "description": "A shared location / Google or Apple Maps link / 'lat,lon' text, pasted as-is"},
        "limit": {"type": "integer", "description": "How many stops (default 3)"},
    }},
}

SHOP_PRODUCT = {
    "name": "shop_product",
    "description": "Get full details for one product id from search results (includes the exact product image URL).",
    "parameters": {"type": "object", "properties": {"product_id": _PID}, "required": ["product_id"]},
}

CART_ADD = {
    "name": "cart_add",
    "description": (
        "Add products to the customer's cart — one (product_id + quantity) or many at once via items, for bulk "
        "orders (up to 1000 of each). Only add AFTER the customer chose a specific brand/item when several options "
        "were shown. Returns the cart with images, delivery fee and total."
    ),
    "parameters": {"type": "object", "properties": {
        "product_id": _PID,
        "quantity": {"type": "integer", "description": "How many (default 1, max 1000)"},
        "items": {"type": "array", "description": "Bulk: several products at once",
                  "items": {"type": "object", "properties": {"product_id": _PID, "quantity": {"type": "integer"}},
                            "required": ["product_id"]}},
    }},
}

CART_UPDATE = {
    "name": "cart_update",
    "description": "Set the exact quantity of an item already in the cart (0 removes it).",
    "parameters": {"type": "object", "properties": {"product_id": _PID,
                   "quantity": {"type": "integer", "description": "New quantity, 0-1000"}}, "required": ["product_id", "quantity"]},
}

CART_REMOVE = {
    "name": "cart_remove",
    "description": "Remove a product (or reduce its quantity) from the cart.",
    "parameters": {"type": "object", "properties": {"product_id": _PID,
                   "quantity": {"type": "integer", "description": "How many to remove; omit to remove all"}}, "required": ["product_id"]},
}

CART_VIEW = {
    "name": "cart_view",
    "description": "Show the cart with line totals, product images and subtotal.",
    "parameters": {"type": "object", "properties": {}},
}

CART_CLEAR = {
    "name": "cart_clear",
    "description": "Empty the cart (only when the user asks).",
    "parameters": {"type": "object", "properties": {}},
}

DELIVERY_QUOTE = {
    "name": "delivery_quote",
    "description": "Quote doorstep delivery and pickup-stop collection (fee from distance + item size, and ETA) for the current cart.",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string", "description": "Optional; defaults to the saved location"},
        "state": {"type": "string"},
        "lat": {"type": "number", "description": "Latitude from a shared location pin"},
        "lon": {"type": "number", "description": "Longitude from a shared location pin"},
        "shared_location": {"type": "string", "description": "A shared location / Google or Apple Maps link / 'lat,lon' text, pasted as-is"},
        "delivery_method": {"type": "string", "enum": ["doorstep", "pickup"], "description": "Doorstep (default) or collect at a pickup stop (cheaper)"},
        "pickup_stop_id": {"type": "string", "description": "Pickup stop id; defaults to the nearest"},
        "speed": {"type": "string", "enum": ["standard", "express"]},
    }},
}

PAYMENT_OPTIONS = {
    "name": "payment_options",
    "description": "List enabled payment methods: Paystack, saved cards (pass email), crypto, and whether bank transfers are enabled.",
    "parameters": {"type": "object", "properties": {
        "email": {"type": "string", "description": "Customer email — returns any reusable saved cards for that address"},
    }},
}

CHECKOUT = {
    "name": "checkout",
    "description": (
        "Place an order for the cart. First call WITHOUT confirm to get a summary (with product images), show it "
        "to the user, and only call again with confirm=true after they explicitly agree. Returns a Paystack payment "
        "link, or a crypto deposit address and exact amount. Never tell the user an order is paid until order_status says paid."
    ),
    "parameters": {"type": "object", "properties": {
        "full_name": {"type": "string"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
        "address": {"type": "string", "description": "Street address and landmark (not needed for pickup)"},
        "city": {"type": "string", "description": "Optional if set_location was called"},
        "state": {"type": "string"},
        "delivery_method": {"type": "string", "enum": ["doorstep", "pickup"], "description": "Doorstep (default) or collect at a pickup stop (cheaper)"},
        "pickup_stop_id": {"type": "string", "description": "Pickup stop id; defaults to the nearest"},
        "speed": {"type": "string", "enum": ["standard", "express"]},
        "payment_method": {"type": "string", "enum": ["paystack", "saved_card", "crypto"], "description": "Default paystack; saved_card charges a reusable authorization"},
        "card_id": {"type": "string", "description": "For saved_card: id from payment_options/saved_cards (signature)"},
        "currency": {"type": "string", "description": "Paystack currency: NGN, GHS, KES, ZAR, USD, XOF, EGP or RWF (default: cart currency)"},
        "crypto_coin": {"type": "string", "description": "For crypto: usdt, usdc, btc, eth, sol, bnb, trx, ltc (default usdt on TRC20)"},
        "confirm": {"type": "boolean", "description": "true only after the user approved the summary"},
    }, "required": ["full_name", "email", "phone"]},
}

ORDER_STATUS = {
    "name": "order_status",
    "description": (
        "Look up orders instantly by order_id, customer email, phone, or (with no args) this conversation's recent "
        "orders. With refresh=true (or a single pending order by id) verifies via Paystack GET /transaction/verify "
        "and marks paid only when amount+currency match; reusable card authorizations are saved (last4/brand only). "
        "Always show each item's image."
    ),
    "parameters": {"type": "object", "properties": {
        "order_id": {"type": "string"},
        "email": {"type": "string", "description": "Find orders for this customer email"},
        "phone": {"type": "string", "description": "Find orders for this customer phone"},
        "refresh": {"type": "boolean", "description": "true to re-check payment with Paystack/NOWPayments"},
        "limit": {"type": "integer", "description": "Max orders to return (default 10)"},
    }},
}

REQUEST_REFUND = {
    "name": "request_refund",
    "description": (
        "Refund a paid, not-yet-delivered order (14-day window). Preview without confirm, then confirm=true. "
        "refund_method=paystack (default card refund API), bank_transfer (NGN payout via Transfers — requires "
        "SHOPMATE_ALLOW_TRANSFERS=1; always show the resolved account_name before confirm), or crypto (manual "
        "payout_address). Amount capped at the paid total. Never double-refund; bank transfers use an idempotent reference."
    ),
    "parameters": {"type": "object", "properties": {
        "order_id": {"type": "string"},
        "reason": {"type": "string", "description": "Why the customer wants a refund"},
        "refund_method": {"type": "string", "enum": ["paystack", "bank_transfer", "crypto"]},
        "amount": {"type": "number", "description": "Optional partial refund; defaults to full paid amount"},
        "bank_code": {"type": "string", "description": "For bank_transfer: Paystack bank code from the bank list"},
        "account_number": {"type": "string", "description": "For bank_transfer: NUBAN account number"},
        "account_name": {"type": "string", "description": "For bank_transfer confirm: must match the resolved name"},
        "payout_address": {"type": "string", "description": "Required for crypto: wallet address to receive the refund"},
        "confirm": {"type": "boolean", "description": "true only after the customer approved the refund summary"},
    }, "required": ["order_id", "reason"]},
}

SAVED_CARDS = {
    "name": "saved_cards",
    "description": (
        "List or remove reusable Paystack cards for a customer email (last4/brand/bank only — never full PAN). "
        "action=bin optionally enriches a card via Card BIN resolution. BVN and phone resolution are not used."
    ),
    "parameters": {"type": "object", "properties": {
        "email": {"type": "string"},
        "action": {"type": "string", "enum": ["list", "remove", "bin"], "description": "Default list"},
        "card_id": {"type": "string", "description": "Required for remove/bin"},
        "bin": {"type": "string", "description": "Optional 6-digit BIN for action=bin"},
    }, "required": ["email"]},
}

CONTACT_SUPPORT = {
    "name": "contact_support",
    "description": (
        "Built-in support: action=faq for short answers (delivery, payments, refunds), action=open to create a "
        "ticket tied to an order (missing/damaged/late/wrong_item/payment/other), action=status to check a ticket. "
        "Shows SUPPORT_EMAIL / SUPPORT_WHATSAPP when configured."
    ),
    "parameters": {"type": "object", "properties": {
        "action": {"type": "string", "enum": ["faq", "open", "status", "list"], "description": "Default faq"},
        "topic": {"type": "string", "description": "FAQ topic filter, e.g. 'refund' or 'delivery'"},
        "question": {"type": "string", "description": "Optional free-text FAQ query"},
        "order_id": {"type": "string"},
        "ticket_id": {"type": "string"},
        "issue_type": {"type": "string", "enum": ["missing", "damaged", "late", "wrong_item", "payment", "other", "refund"]},
        "subject": {"type": "string"},
        "message": {"type": "string", "description": "Ticket body when action=open"},
        "email": {"type": "string"},
        "phone": {"type": "string"},
    }},
}
