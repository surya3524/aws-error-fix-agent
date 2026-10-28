DISCOUNTS = {"SAVE10": 0.10, "SAVE20": 0.20}


def apply_discount(amount, coupon):
    rate = DISCOUNTS[coupon]
    return round(amount * (1 - rate), 2)
