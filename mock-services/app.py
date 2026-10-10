"""
Mock backends for local development of the Omnichannel Integration Hub.

One FastAPI app serves five systems under path prefixes, mimicking the real
request/response shapes closely enough that the Mule flows run unchanged:

  /shopify     Shopify Admin REST API (orders, products, inventory_levels/set)
  /amazon      Login with Amazon token + SP-API Orders v0 + Listings 2021-08-01
  /sapb1       SAP Business One Service Layer (Login, Orders, BusinessPartners)
  /salesforce  OAuth client credentials + SOQL query endpoint
  /wms         Warehouse inventory export as CSV

Behaviour knobs (env vars):
  SHOPIFY_ORDERS_PER_POLL  max new Shopify orders per poll    (default 4)
  AMAZON_ORDERS_PER_POLL   max new Amazon orders per poll     (default 4)
  SAP_TRANSIENT_FAILURE_RATE  share of SAP order POSTs that return 503 (default 0.02)
  BAD_SKU_RATE             share of orders carrying an unknown SKU (default 0.01)
  MOCK_SEED                random seed for reproducible runs

With a 2-minute poll, the defaults average ~5K orders/day across both channels.
"""

from __future__ import annotations

import csv
import io
import os
import random
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

SHOPIFY_TOKEN = os.getenv("MOCK_SHOPIFY_TOKEN", "mock-shopify-token")
WMS_API_KEY = os.getenv("MOCK_WMS_API_KEY", "mock-wms-key")
SHOPIFY_ORDERS_PER_POLL = int(os.getenv("SHOPIFY_ORDERS_PER_POLL", "4"))
AMAZON_ORDERS_PER_POLL = int(os.getenv("AMAZON_ORDERS_PER_POLL", "4"))
SAP_TRANSIENT_FAILURE_RATE = float(os.getenv("SAP_TRANSIENT_FAILURE_RATE", "0.02"))
BAD_SKU_RATE = float(os.getenv("BAD_SKU_RATE", "0.01"))

rng = random.Random(int(os.getenv("MOCK_SEED", "42")))
lock = threading.Lock()

app = FastAPI(title="Omnichannel Hub Mock Services", version="1.0.0")

# --------------------------------------------------------------------------- #
# Shared reference data
# --------------------------------------------------------------------------- #
CATALOG: list[dict[str, Any]] = []
_families = [
    ("GLV-CUT-A4", "Cut Resistant Glove A4", 18.99, ["S", "M", "L", "XL"]),
    ("GLV-CUT-A6", "Cut Resistant Glove A6", 29.99, ["S", "M", "L", "XL"]),
    ("GLV-IMPACT", "Impact Glove", 24.99, ["M", "L", "XL"]),
    ("GLV-NEEDLE", "Needlestick Resistant Glove", 39.99, ["M", "L"]),
    ("SLV-CUT-A5", "Cut Resistant Sleeve A5", 22.99, ["12", "18", "22"]),
    ("GLS-SAFETY", "Safety Glasses", 14.99, ["CLR", "SMK", "AMB"]),
    ("GLS-GOGGLE", "Anti-Fog Goggle", 27.50, ["CLR"]),
    ("VST-HIVIS", "Hi-Vis Vest", 19.99, ["M", "L", "XL", "2XL"]),
]
for prefix, title, price, sizes in _families:
    for size in sizes:
        CATALOG.append({"sku": f"{prefix}-{size}", "title": f"{title} - {size}", "price": price})

SKU_INDEX = {item["sku"]: item for item in CATALOG}
BAD_SKU = "DISC-ITEM-0001"  # not in SAP item master -> SAP 400 (non-retryable)

FIRST_NAMES = ["Jordan", "Priya", "Marcus", "Elena", "Sam", "Aisha", "Diego", "Mei", "Tom", "Grace"]
LAST_NAMES = ["Lee", "Raman", "Johnson", "Garcia", "Nguyen", "Okafor", "Smith", "Kowalski", "Chen", "Brown"]
CITIES = [
    ("Grand Rapids", "MI", "49503"), ("Detroit", "MI", "48226"), ("Chicago", "IL", "60601"),
    ("Houston", "TX", "77002"), ("Denver", "CO", "80202"), ("Atlanta", "GA", "30303"),
    ("Pittsburgh", "PA", "15222"), ("Columbus", "OH", "43215"),
]

STATE: dict[str, Any] = {
    "shopify_next_id": 5_500_000_000_000,
    "shopify_orders": [],          # generated so far (for replaying "updated" orders)
    "amazon_orders": {},           # AmazonOrderId -> (order, items)
    "sap_sessions": set(),
    "sap_orders": {},              # NumAtCard -> order
    "sap_next_doc_entry": 1000,
    "sap_bps": {},                 # CardCode -> bp
    "sf_tokens": set(),
    "amz_tokens": set(),
    "inventory_updates": {"shopify": 0, "amazon": 0},
    "stats": {"sap_order_posts": 0, "sap_transient_failures": 0, "sap_validation_failures": 0},
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")


def _pick_lines(max_lines: int = 3) -> list[tuple[dict[str, Any], int]]:
    lines = [(rng.choice(CATALOG), rng.randint(1, 4)) for _ in range(rng.randint(1, max_lines))]
    if rng.random() < BAD_SKU_RATE:
        lines.append(({"sku": BAD_SKU, "title": "Discontinued Item", "price": 9.99}, 1))
    return lines


def _person() -> tuple[str, str]:
    return rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)


# --------------------------------------------------------------------------- #
# Shopify
# --------------------------------------------------------------------------- #
def _require_shopify(token: str | None) -> None:
    if token != SHOPIFY_TOKEN:
        raise HTTPException(status_code=401, detail={"errors": "[API] Invalid API key or access token"})


def _new_shopify_order() -> dict[str, Any]:
    with lock:
        STATE["shopify_next_id"] += 1
        order_id = STATE["shopify_next_id"]
    first, last = _person()
    city, state, zip_code = rng.choice(CITIES)
    created = _now() - timedelta(seconds=rng.randint(0, 110))
    line_items = []
    subtotal = tax_total = discount_total = 0.0
    for item, qty in _pick_lines():
        discount = round(item["price"] * qty * rng.choice([0, 0, 0.1]), 2)
        tax = round((item["price"] * qty - discount) * 0.06, 2)
        subtotal += item["price"] * qty - discount
        tax_total += tax
        discount_total += discount
        line_items.append({
            "id": rng.randint(10**12, 10**13),
            "sku": item["sku"].lower() if rng.random() < 0.2 else item["sku"],  # messy casing on purpose
            "title": item["title"],
            "quantity": qty,
            "price": f"{item['price']:.2f}",
            "total_discount": f"{discount:.2f}",
            "tax_lines": [{"title": "MI State Tax", "rate": 0.06, "price": f"{tax:.2f}"}],
        })
    shipping = 0.0 if subtotal > 75 else 8.95
    total = subtotal + tax_total + shipping
    return {
        "id": order_id,
        "name": f"#{order_id % 100000}",
        "email": f"{first}.{last}{rng.randint(1, 999)}@example.com",
        "created_at": created.isoformat(timespec="seconds"),
        "updated_at": _now().isoformat(timespec="seconds"),
        "currency": "USD",
        "financial_status": "paid",
        "subtotal_price": f"{subtotal:.2f}",
        "total_tax": f"{tax_total:.2f}",
        "total_discounts": f"{discount_total:.2f}",
        "total_price": f"{total:.2f}",
        "customer": {"id": rng.randint(10**9, 10**10), "first_name": first, "last_name": last, "phone": None},
        "shipping_address": {
            "name": f"{first} {last}", "address1": f"{rng.randint(100, 9999)} Main St", "address2": None,
            "city": city, "province_code": state, "zip": zip_code, "country_code": "US", "phone": None,
        },
        "line_items": line_items,
        "shipping_lines": [{"title": "Standard", "price": f"{shipping:.2f}"}],
    }


@app.get("/shopify/admin/api/{version}/orders.json")
def shopify_orders(version: str, x_shopify_access_token: str | None = Header(default=None)):
    _require_shopify(x_shopify_access_token)
    new_orders = [_new_shopify_order() for _ in range(rng.randint(0, SHOPIFY_ORDERS_PER_POLL))]
    with lock:
        previous = list(STATE["shopify_orders"][-50:])
        STATE["shopify_orders"].extend(new_orders)
    # ~10% of polls also re-send an already-seen order (an "update"), exercising idempotency
    replayed = [rng.choice(previous)] if previous and rng.random() < 0.1 else []
    return {"orders": new_orders + replayed}


@app.get("/shopify/admin/api/{version}/products.json")
def shopify_products(version: str, x_shopify_access_token: str | None = Header(default=None)):
    _require_shopify(x_shopify_access_token)
    products: dict[str, dict[str, Any]] = {}
    for idx, item in enumerate(CATALOG):
        family = item["sku"].rsplit("-", 1)[0]
        product = products.setdefault(family, {"id": 8_000_000 + len(products), "title": family, "variants": []})
        product["variants"].append({
            "id": 9_000_000 + idx, "sku": item["sku"], "inventory_item_id": 4_000_000_000 + idx,
        })
    return {"products": list(products.values())}


@app.post("/shopify/admin/api/{version}/inventory_levels/set.json")
async def shopify_set_inventory(version: str, request: Request,
                                x_shopify_access_token: str | None = Header(default=None)):
    _require_shopify(x_shopify_access_token)
    body = await request.json()
    missing = [k for k in ("location_id", "inventory_item_id", "available") if k not in body]
    if missing:
        raise HTTPException(status_code=422, detail={"errors": {k: ["is required"] for k in missing}})
    with lock:
        STATE["inventory_updates"]["shopify"] += 1
    return {"inventory_level": {**body, "updated_at": _iso(_now())}}


# --------------------------------------------------------------------------- #
# Amazon (LWA + SP-API)
# --------------------------------------------------------------------------- #
@app.post("/amazon/auth/o2/token")
async def amazon_token(request: Request):
    form = await request.form()
    if form.get("grant_type") != "refresh_token" or not form.get("refresh_token"):
        return JSONResponse(status_code=400, content={"error": "invalid_grant"})
    token = "Atza|" + uuid.uuid4().hex
    with lock:
        STATE["amz_tokens"].add(token)
    return {"access_token": token, "token_type": "bearer", "expires_in": 3600}


def _require_amazon(token: str | None) -> None:
    if token not in STATE["amz_tokens"]:
        raise HTTPException(status_code=403, detail={"errors": [{"code": "Unauthorized", "message": "Access to requested resource is denied."}]})


def _new_amazon_order() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    order_id = f"11{rng.randint(1, 9)}-{rng.randint(1000000, 9999999)}-{rng.randint(1000000, 9999999)}"
    first, last = _person()
    city, state, zip_code = rng.choice(CITIES)
    items = []
    total = 0.0
    for item, qty in _pick_lines(2):
        line = round(item["price"] * qty, 2)
        tax = round(line * 0.06, 2)
        promo = round(line * rng.choice([0, 0, 0.15]), 2)
        total += line + tax - promo
        items.append({
            "OrderItemId": str(rng.randint(10**13, 10**14)),
            "SellerSKU": item["sku"], "Title": item["title"], "QuantityOrdered": qty,
            "ItemPrice": {"CurrencyCode": "USD", "Amount": f"{line:.2f}"},
            "ItemTax": {"CurrencyCode": "USD", "Amount": f"{tax:.2f}"},
            "ShippingPrice": {"CurrencyCode": "USD", "Amount": "0.00"},
            "PromotionDiscount": {"CurrencyCode": "USD", "Amount": f"{promo:.2f}"},
        })
    now = _now()
    order = {
        "AmazonOrderId": order_id,
        "PurchaseDate": _iso(now - timedelta(minutes=rng.randint(1, 30))),
        "LastUpdateDate": _iso(now),
        "OrderStatus": "Unshipped",
        "FulfillmentChannel": "MFN",
        "OrderTotal": {"CurrencyCode": "USD", "Amount": f"{total:.2f}"},
        "MarketplaceId": "ATVPDKIKX0DER",
        "BuyerInfo": {"BuyerEmail": f"{uuid.uuid4().hex[:14]}@marketplace.amazon.com"},
        "ShippingAddress": {
            "Name": f"{first} {last}", "AddressLine1": f"{rng.randint(100, 9999)} Oak Ave",
            "City": city, "StateOrRegion": state, "PostalCode": zip_code, "CountryCode": "US",
        },
    }
    return order, items


@app.get("/amazon/orders/v0/orders")
def amazon_orders(MarketplaceIds: str, x_amz_access_token: str | None = Header(default=None)):
    _require_amazon(x_amz_access_token)
    orders = []
    for _ in range(rng.randint(0, AMAZON_ORDERS_PER_POLL)):
        order, items = _new_amazon_order()
        with lock:
            STATE["amazon_orders"][order["AmazonOrderId"]] = (order, items)
        orders.append(order)
    return {"payload": {"Orders": orders, "CreatedBefore": _iso(_now())}}


@app.get("/amazon/orders/v0/orders/{order_id}/orderItems")
def amazon_order_items(order_id: str, x_amz_access_token: str | None = Header(default=None)):
    _require_amazon(x_amz_access_token)
    if order_id not in STATE["amazon_orders"]:
        raise HTTPException(status_code=404, detail={"errors": [{"code": "InvalidInput", "message": "Invalid order id"}]})
    _, items = STATE["amazon_orders"][order_id]
    return {"payload": {"AmazonOrderId": order_id, "OrderItems": items}}


@app.patch("/amazon/listings/2021-08-01/items/{seller_id}/{sku}")
async def amazon_patch_listing(seller_id: str, sku: str, marketplaceIds: str, request: Request,
                               x_amz_access_token: str | None = Header(default=None)):
    _require_amazon(x_amz_access_token)
    body = await request.json()
    if not body.get("patches"):
        return JSONResponse(status_code=400, content={"errors": [{"code": "InvalidInput", "message": "patches required"}]})
    with lock:
        STATE["inventory_updates"]["amazon"] += 1
    return {"sku": sku, "status": "ACCEPTED", "submissionId": uuid.uuid4().hex, "issues": []}


# --------------------------------------------------------------------------- #
# SAP Business One Service Layer
# --------------------------------------------------------------------------- #
def _sap_error(status: int, code: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": {"lang": "en-us", "value": message}}})


def _sap_session(cookie: str | None) -> bool:
    if not cookie:
        return False
    parts = dict(p.strip().split("=", 1) for p in cookie.split(";") if "=" in p)
    return parts.get("B1SESSION") in STATE["sap_sessions"]


@app.post("/sapb1/b1s/v1/Login")
async def sap_login(request: Request, response: Response):
    body = await request.json()
    if not body.get("CompanyDB") or not body.get("UserName") or not body.get("Password"):
        return _sap_error(401, -304, "Fail to get DB Credentials from SLD")
    session = str(uuid.uuid4())
    with lock:
        STATE["sap_sessions"].add(session)
    response.set_cookie("B1SESSION", session, httponly=True)
    return {"SessionId": session, "Version": "1000190", "SessionTimeout": 30}


@app.get("/sapb1/b1s/v1/Orders")
def sap_find_orders(request: Request, cookie: str | None = Header(default=None)):
    if not _sap_session(cookie):
        return _sap_error(401, 301, "Invalid session or session already timeout.")
    filt = request.query_params.get("$filter", "")
    value = []
    if filt.startswith("NumAtCard eq '"):
        key = filt[len("NumAtCard eq '"):-1]
        order = STATE["sap_orders"].get(key)
        if order:
            value.append({"DocEntry": order["DocEntry"], "DocNum": order["DocNum"]})
    return {"value": value}


@app.post("/sapb1/b1s/v1/Orders")
async def sap_create_order(request: Request, cookie: str | None = Header(default=None)):
    if not _sap_session(cookie):
        return _sap_error(401, 301, "Invalid session or session already timeout.")
    body = await request.json()
    with lock:
        STATE["stats"]["sap_order_posts"] += 1
    if rng.random() < SAP_TRANSIENT_FAILURE_RATE:
        with lock:
            STATE["stats"]["sap_transient_failures"] += 1
        return JSONResponse(status_code=503, content={"error": "Service Layer temporarily unavailable"})

    lines = body.get("DocumentLines") or []
    problem = None
    if not body.get("CardCode"):
        problem = "CardCode is required"
    elif not lines:
        problem = "Document must have at least one line"
    else:
        unknown = [l.get("ItemCode") for l in lines if l.get("ItemCode") not in SKU_INDEX]
        if unknown:
            problem = f"Invalid ItemCode: {', '.join(map(str, unknown))}"
    if problem:
        with lock:
            STATE["stats"]["sap_validation_failures"] += 1
        return _sap_error(400, -5002, problem)

    with lock:
        if body.get("NumAtCard") in STATE["sap_orders"]:
            return _sap_error(400, -2035, "Duplicate NumAtCard")
        STATE["sap_next_doc_entry"] += 1
        doc_entry = STATE["sap_next_doc_entry"]
        created = {**body, "DocEntry": doc_entry, "DocNum": doc_entry + 4000, "DocumentStatus": "bost_Open"}
        STATE["sap_orders"][body.get("NumAtCard")] = created
    return JSONResponse(status_code=201, content=created)


@app.get("/sapb1/b1s/v1/BusinessPartners('{card_code}')")
def sap_get_bp(card_code: str, cookie: str | None = Header(default=None)):
    if not _sap_session(cookie):
        return _sap_error(401, 301, "Invalid session or session already timeout.")
    bp = STATE["sap_bps"].get(card_code)
    if not bp:
        return _sap_error(404, -2028, "No matching records found (ODBC -2028)")
    return bp


@app.post("/sapb1/b1s/v1/BusinessPartners")
async def sap_create_bp(request: Request, cookie: str | None = Header(default=None)):
    if not _sap_session(cookie):
        return _sap_error(401, 301, "Invalid session or session already timeout.")
    body = await request.json()
    code = body.get("CardCode")
    if not code or len(code) > 15:
        return _sap_error(400, -10, "CardCode is required and must be 15 characters or fewer")
    with lock:
        STATE["sap_bps"][code] = body
    return JSONResponse(status_code=201, content=body)


@app.patch("/sapb1/b1s/v1/BusinessPartners('{card_code}')")
async def sap_update_bp(card_code: str, request: Request, cookie: str | None = Header(default=None)):
    if not _sap_session(cookie):
        return _sap_error(401, 301, "Invalid session or session already timeout.")
    if card_code not in STATE["sap_bps"]:
        return _sap_error(404, -2028, "No matching records found (ODBC -2028)")
    body = await request.json()
    with lock:
        STATE["sap_bps"][card_code].update(body)
    return Response(status_code=204)


# --------------------------------------------------------------------------- #
# Salesforce
# --------------------------------------------------------------------------- #
ACCOUNT_POOL = []
for i in range(60):
    ACCOUNT_POOL.append({
        "Id": f"001Dn0000{i:03d}AbCdIAV"[:18],
        "Name": f"{rng.choice(['Lakeshore', 'Summit', 'Ironworks', 'Keystone', 'Northern', 'Riverbend'])} "
                f"{rng.choice(['Fabrication', 'Utility Services', 'Construction', 'Logistics', 'Metalworks'])} {i}",
        "AccountNumber": f"AC-{10000 + i}" if i % 4 else None,
        "Type": rng.choice(["Customer - Direct", "Customer - Channel", None]),
        "Industry": rng.choice(["Manufacturing", "Utilities", "Construction", "Energy", "Transportation"]),
        "Phone": f"(616) 555-{1000 + i:04d}",
        "Website": None,
        "BillingStreet": f"{100 + i} Industrial Pkwy",
        "BillingCity": rng.choice(CITIES)[0],
        "BillingState": "MI",
        "BillingPostalCode": "49503",
        "BillingCountry": "US",
    })


@app.post("/salesforce/services/oauth2/token")
async def salesforce_token(request: Request):
    form = await request.form()
    if form.get("grant_type") != "client_credentials" or not form.get("client_id"):
        return JSONResponse(status_code=400, content={"error": "invalid_client", "error_description": "invalid client credentials"})
    token = "00D" + uuid.uuid4().hex
    with lock:
        STATE["sf_tokens"].add(token)
    return {"access_token": token, "instance_url": "http://localhost:8090/salesforce", "token_type": "Bearer",
            "issued_at": str(int(_now().timestamp() * 1000))}


@app.get("/salesforce/services/data/{version}/query")
def salesforce_query(version: str, q: str, authorization: str | None = Header(default=None)):
    if not authorization or authorization.removeprefix("Bearer ").strip() not in STATE["sf_tokens"]:
        return JSONResponse(status_code=401, content=[{"message": "Session expired or invalid", "errorCode": "INVALID_SESSION_ID"}])
    if "FROM Account" not in q:
        return JSONResponse(status_code=400, content=[{"message": "sObject type not supported by mock", "errorCode": "INVALID_TYPE"}])
    changed = rng.sample(ACCOUNT_POOL, rng.randint(3, 10))
    records = []
    for acct in changed:
        records.append({
            "attributes": {"type": "Account", "url": f"/services/data/{version}/sobjects/Account/{acct['Id']}"},
            **acct,
            "LastModifiedDate": _now().strftime("%Y-%m-%dT%H:%M:%S.000+0000"),
        })
    return {"totalSize": len(records), "done": True, "records": records}


# --------------------------------------------------------------------------- #
# WMS
# --------------------------------------------------------------------------- #
@app.get("/wms/api/v1/inventory/export")
def wms_inventory(format: str = "csv", x_api_key: str | None = Header(default=None)):
    if x_api_key != WMS_API_KEY:
        raise HTTPException(status_code=401, detail="invalid api key")
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["sku", "warehouse_code", "on_hand", "allocated", "damaged", "updated_at"])
    stamp = _iso(_now())
    for item in CATALOG:
        for wh in ("WH-EAST", "WH-WEST"):
            on_hand = rng.randint(0, 400)
            writer.writerow([item["sku"], wh, on_hand, rng.randint(0, min(on_hand, 40)), rng.choice(["", 0, 0, 1]), stamp])
    return PlainTextResponse(buf.getvalue(), media_type="text/csv")


# --------------------------------------------------------------------------- #
# Mock introspection
# --------------------------------------------------------------------------- #
@app.get("/_mock/stats")
def mock_stats():
    return {
        "shopify_orders_generated": len(STATE["shopify_orders"]),
        "amazon_orders_generated": len(STATE["amazon_orders"]),
        "sap_orders_created": len(STATE["sap_orders"]),
        "sap_business_partners": len(STATE["sap_bps"]),
        "inventory_updates": STATE["inventory_updates"],
        **STATE["stats"],
    }


@app.post("/_mock/reset")
def mock_reset():
    with lock:
        STATE["shopify_orders"].clear()
        STATE["amazon_orders"].clear()
        STATE["sap_orders"].clear()
        STATE["sap_bps"].clear()
        STATE["inventory_updates"] = {"shopify": 0, "amazon": 0}
        STATE["stats"] = {"sap_order_posts": 0, "sap_transient_failures": 0, "sap_validation_failures": 0}
    return {"reset": True}


@app.get("/health")
def health():
    return {"status": "UP"}
