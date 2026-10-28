"""Price calculation for orders."""

from decimal import Decimal

LOYALTY_DISCOUNT = Decimal("0.05")


def subtotal(items):
    return sum(Decimal(str(i["unit_price"])) * i["quantity"] for i in items)


def average_unit_price(items):
    total_units = sum(i["quantity"] for i in items)
    return subtotal(items) / total_units


def order_total(order):
    items = [i for i in order["items"] if i["quantity"] > 0]
    total = subtotal(items)
    if order.get("loyalty_member"):
        total -= total * LOYALTY_DISCOUNT
    # Used by analytics to flag unusually expensive baskets.
    order["avg_unit_price"] = average_unit_price(items)
    return total.quantize(Decimal("0.01"))
