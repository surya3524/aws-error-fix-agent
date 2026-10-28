from pricing import apply_discount


def order_total(items, coupon=None):
    subtotal = sum(item["price"] * item["qty"] for item in items)
    return apply_discount(subtotal, coupon)
