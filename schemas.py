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
        "Show each result with its image as markdown ![title](image), price and merchant. "
        "To browse a section, pass category with an empty query."
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
    "description": "Quote the delivery fee and estimated delivery time for the current cart to a city.",
    "parameters": {"type": "object", "properties": {
        "city": {"type": "string"},
        "speed": {"type": "string", "enum": ["standard", "express"]},
    }, "required": ["city"]},
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
        "address": {"type": "string", "description": "Street address and landmark"},
        "city": {"type": "string"},
        "speed": {"type": "string", "enum": ["standard", "express"]},
        "payment_method": {"type": "string", "enum": ["paystack", "crypto"], "description": "Default paystack"},
        "currency": {"type": "string", "description": "Paystack currency: NGN, GHS, KES, ZAR, USD, XOF, EGP or RWF (default: cart currency)"},
        "crypto_coin": {"type": "string", "description": "For crypto: usdt, usdc, btc, eth, sol, bnb, trx, ltc (default usdt on TRC20)"},
        "confirm": {"type": "boolean", "description": "true only after the user approved the summary"},
    }, "required": ["full_name", "email", "phone", "address", "city"]},
}

ORDER_STATUS = {
    "name": "order_status",
    "description": "Check payment and delivery status of an order (verifies with the payment provider). Omit order_id to list this conversation's orders.",
    "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}},
}
