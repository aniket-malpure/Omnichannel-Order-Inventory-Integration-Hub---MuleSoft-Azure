#!/usr/bin/env python3
"""
Load generator: sends signed Shopify orders/create webhooks to the hub.

Example (local, ~5K orders spread over a simulated day at 60x speed):
    python scripts/send_shopify_webhooks.py --count 5000 --rate 3.5

Uses only the standard library.
"""

import argparse
import base64
import hashlib
import hmac
import json
import random
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SKUS = [
    ("GLV-CUT-A6-L", "Cut Resistant Glove A6 - L", 29.99),
    ("GLV-CUT-A4-M", "Cut Resistant Glove A4 - M", 18.99),
    ("GLV-IMPACT-XL", "Impact Glove - XL", 24.99),
    ("SLV-CUT-A5-18", "Cut Resistant Sleeve A5 - 18", 22.99),
    ("GLS-SAFETY-CLR", "Safety Glasses - CLR", 14.99),
    ("VST-HIVIS-L", "Hi-Vis Vest - L", 19.99),
]


def build_order(order_id: int) -> dict:
    lines = []
    subtotal = 0.0
    for sku, title, price in random.sample(SKUS, random.randint(1, 3)):
        qty = random.randint(1, 4)
        subtotal += price * qty
        lines.append({"sku": sku, "title": title, "quantity": qty, "price": f"{price:.2f}",
                      "total_discount": "0.00", "tax_lines": [{"price": f"{price * qty * 0.06:.2f}"}]})
    tax = round(subtotal * 0.06, 2)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {
        "id": order_id, "name": f"#W{order_id % 100000}", "email": f"buyer{order_id % 997}@example.com",
        "created_at": now, "updated_at": now, "currency": "USD", "financial_status": "paid",
        "subtotal_price": f"{subtotal:.2f}", "total_tax": f"{tax:.2f}", "total_discounts": "0.00",
        "total_price": f"{subtotal + tax:.2f}",
        "customer": {"id": order_id % 50000, "first_name": "Load", "last_name": "Test"},
        "shipping_address": {"name": "Load Test", "address1": "1 Test Way", "city": "Grand Rapids",
                             "province_code": "MI", "zip": "49503", "country_code": "US"},
        "line_items": lines, "shipping_lines": [{"price": "0.00"}],
    }


def sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://localhost:8081/api/v1/webhooks/shopify/orders")
    parser.add_argument("--secret", default="local-webhook-secret")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--rate", type=float, default=5.0, help="requests per second")
    parser.add_argument("--start-id", type=int, default=int(time.time()) * 1000)
    args = parser.parse_args()

    ok = failed = 0
    started = time.time()
    for n in range(args.count):
        body = json.dumps(build_order(args.start_id + n)).encode()
        req = urllib.request.Request(args.url, data=body, method="POST", headers={
            "Content-Type": "application/json",
            "X-Shopify-Topic": "orders/create",
            "X-Shopify-Hmac-Sha256": sign(args.secret, body),
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                ok += resp.status == 202
        except urllib.error.HTTPError as exc:
            failed += 1
            print(f"order {args.start_id + n}: HTTP {exc.code} {exc.read()[:200]!r}", file=sys.stderr)
        except urllib.error.URLError as exc:
            print(f"Cannot reach {args.url}: {exc.reason}", file=sys.stderr)
            return 1
        time.sleep(max(0.0, (n + 1) / args.rate - (time.time() - started)))

    elapsed = time.time() - started
    print(f"Sent {args.count} webhooks in {elapsed:.1f}s: {ok} accepted, {failed} failed")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
