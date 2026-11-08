"""Shopmate — shopping agent plugin for Hermes."""

import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)

TOOLS = [
    ("shop_categories", schemas.SHOP_CATEGORIES, tools.shop_categories),
    ("shop_search", schemas.SHOP_SEARCH, tools.shop_search),
    ("set_location", schemas.SET_LOCATION, tools.set_location),
    ("shop_product", schemas.SHOP_PRODUCT, tools.shop_product),
    ("cart_add", schemas.CART_ADD, tools.cart_add),
    ("cart_remove", schemas.CART_REMOVE, tools.cart_remove),
    ("cart_view", schemas.CART_VIEW, tools.cart_view),
    ("cart_clear", schemas.CART_CLEAR, tools.cart_clear),
    ("delivery_quote", schemas.DELIVERY_QUOTE, tools.delivery_quote),
    ("payment_options", schemas.PAYMENT_OPTIONS, tools.payment_options),
    ("checkout", schemas.CHECKOUT, tools.checkout),
    ("order_status", schemas.ORDER_STATUS, tools.order_status),
]


def register(ctx):
    for name, schema, handler in TOOLS:
        ctx.register_tool(name=name, toolset="shopmate", schema=schema, handler=handler)
    skills_dir = Path(__file__).parent / "skills"
    for child in sorted(skills_dir.iterdir()):
        skill_md = child / "SKILL.md"
        if child.is_dir() and skill_md.exists():
            ctx.register_skill(child.name, skill_md)
    logger.debug("shopmate registered %d tools", len(TOOLS))
