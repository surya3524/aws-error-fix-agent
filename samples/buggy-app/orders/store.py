"""Tiny in-memory stand-in for the orders table."""

_ORDERS = {
    "A100": {"items": [{"sku": "TEA", "unit_price": 4.5, "quantity": 2}], "loyalty_member": True},
    # A customer removed every item from the cart, which leaves quantity 0 lines behind.
    "A101": {"items": [{"sku": "MUG", "unit_price": 12.0, "quantity": 0}], "loyalty_member": False},
}


def load_order(order_id):
    return _ORDERS[order_id]
