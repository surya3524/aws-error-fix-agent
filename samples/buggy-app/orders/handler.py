"""HTTP handler for POST /orders/{id}/checkout."""

import json
import logging

from orders.pricing import order_total
from orders.store import load_order

log = logging.getLogger(__name__)


def checkout(event, context=None):
    order_id = event["pathParameters"]["id"]
    order = load_order(order_id)
    total = order_total(order)
    log.info("order %s total %s", order_id, total)
    return {"statusCode": 200, "body": json.dumps({"order_id": order_id, "total": str(total)})}
