"""Tool schemas — what the model reads."""

_PID = {"type": "string", "description": "Product id from shop_search results (e.g. 'd-pho1' or 'g-…')"}

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
        "Every result carries delivery_display and total_delivered for the customer's saved location — always show "
        "them. best_match is the top pick to present for confirmation. To browse a section, pass category with an empty query."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "What the user wants, e.g. 'Tecno Spark 30' or 'rice 25kg'"},
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
    "description": "Get full details for one product id from search results.",
    "parameters": {"type": "object", "properties": {"product_id": _PID}, "required": ["product_id"]},
}

CART_ADD = {
    "name": "cart_add",
    "description": "Add a product to the user's cart. Only add what the user explicitly chose.",
    "parameters": {"type": "object", "properties": {"product_id": _PID,
                   "quantity": {"type": "integer", "description": "How many (default 1)"}}, "required": ["product_id"]},
}

CART_REMOVE = {
    "name": "cart_remove",
    "description": "Remove a product (or reduce its quantity) from the cart.",
    "parameters": {"type": "object", "properties": {"product_id": _PID,
                   "quantity": {"type": "integer", "description": "How many to remove; omit to remove all"}}, "required": ["product_id"]},
}

CART_VIEW = {
    "name": "cart_view",
    "description": "Show the cart with line totals and subtotal.",
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
    "description": "List enabled payment methods: Paystack local currencies and crypto coins.",
    "parameters": {"type": "object", "properties": {}},
}

CHECKOUT = {
    "name": "checkout",
    "description": (
        "Place an order for the cart. First call WITHOUT confirm to get a summary, show it to the user, and only "
        "call again with confirm=true after they explicitly agree. Returns a Paystack payment link, or a crypto "
        "deposit address and exact amount. Never tell the user an order is paid until order_status says paid."
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
        "payment_method": {"type": "string", "enum": ["paystack", "crypto"], "description": "Default paystack"},
        "currency": {"type": "string", "description": "Paystack currency: NGN, GHS, KES, ZAR, USD, XOF, EGP or RWF (default: cart currency)"},
        "crypto_coin": {"type": "string", "description": "For crypto: usdt, usdc, btc, eth, sol, bnb, trx, ltc (default usdt on TRC20)"},
        "confirm": {"type": "boolean", "description": "true only after the user approved the summary"},
    }, "required": ["full_name", "email", "phone"]},
}

ORDER_STATUS = {
    "name": "order_status",
    "description": "Check payment and delivery status of an order (verifies with the payment provider). Omit order_id to list this conversation's orders.",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
}
