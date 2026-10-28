from orders import order_total


def checkout_handler(request):
    items = request["items"]
    coupon = request.get("coupon")
    return {"total": order_total(items, coupon)}
