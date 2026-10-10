"""Contract tests for the mock backends: they must behave like the real APIs the Mule flows call."""

import os
import sys

os.environ.setdefault("SAP_TRANSIENT_FAILURE_RATE", "0")
os.environ.setdefault("BAD_SKU_RATE", "0")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as mock_app  # noqa: E402

client = TestClient(mock_app.app)
SHOPIFY = {"X-Shopify-Access-Token": "mock-shopify-token"}


@pytest.fixture(autouse=True)
def reset():
    client.post("/_mock/reset")


def sap_cookie() -> dict:
    r = client.post("/sapb1/b1s/v1/Login", json={"CompanyDB": "SBODEMOUS", "UserName": "manager", "Password": "x"})
    assert r.status_code == 200
    return {"Cookie": f"B1SESSION={r.json()['SessionId']}"}


def test_shopify_rejects_bad_token():
    assert client.get("/shopify/admin/api/2025-07/orders.json").status_code == 401


def test_shopify_orders_shape():
    body = client.get("/shopify/admin/api/2025-07/orders.json", headers=SHOPIFY).json()
    assert "orders" in body
    for order in body["orders"]:
        assert {"id", "line_items", "total_price", "shipping_address"} <= order.keys()


def test_shopify_products_expose_inventory_item_ids():
    products = client.get("/shopify/admin/api/2025-07/products.json", headers=SHOPIFY).json()["products"]
    variants = [v for p in products for v in p["variants"]]
    assert len(variants) == len(mock_app.CATALOG)
    assert all("inventory_item_id" in v for v in variants)


def test_amazon_token_then_orders_and_items():
    token = client.post("/amazon/auth/o2/token", data={"grant_type": "refresh_token", "refresh_token": "r",
                                                       "client_id": "c", "client_secret": "s"}).json()["access_token"]
    headers = {"x-amz-access-token": token}
    orders = []
    for _ in range(10):
        orders = client.get("/amazon/orders/v0/orders", params={"MarketplaceIds": "ATVPDKIKX0DER"},
                            headers=headers).json()["payload"]["Orders"]
        if orders:
            break
    assert orders
    items = client.get(f"/amazon/orders/v0/orders/{orders[0]['AmazonOrderId']}/orderItems",
                       headers=headers).json()["payload"]["OrderItems"]
    assert items and "SellerSKU" in items[0]


def test_sap_requires_session():
    assert client.post("/sapb1/b1s/v1/Orders", json={}).status_code == 401


def test_sap_order_create_find_and_duplicate():
    cookie = sap_cookie()
    order = {"CardCode": "C-WEB-SHOPIFY", "NumAtCard": "SHP-1",
             "DocumentLines": [{"ItemCode": "GLV-CUT-A6-L", "Quantity": 2, "UnitPrice": 29.99}]}
    created = client.post("/sapb1/b1s/v1/Orders", json=order, headers=cookie)
    assert created.status_code == 201 and created.json()["DocEntry"] > 0

    found = client.get("/sapb1/b1s/v1/Orders", params={"$filter": "NumAtCard eq 'SHP-1'"}, headers=cookie).json()
    assert found["value"][0]["DocEntry"] == created.json()["DocEntry"]

    assert client.post("/sapb1/b1s/v1/Orders", json=order, headers=cookie).status_code == 400


def test_sap_rejects_unknown_item_with_sap_error_shape():
    cookie = sap_cookie()
    r = client.post("/sapb1/b1s/v1/Orders", headers=cookie, json={
        "CardCode": "C1", "NumAtCard": "SHP-2", "DocumentLines": [{"ItemCode": "NOPE", "Quantity": 1}]})
    assert r.status_code == 400
    assert "Invalid ItemCode" in r.json()["error"]["message"]["value"]


def test_sap_business_partner_upsert():
    cookie = sap_cookie()
    assert client.get("/sapb1/b1s/v1/BusinessPartners('CAC-10001')", headers=cookie).status_code == 404
    assert client.post("/sapb1/b1s/v1/BusinessPartners", headers=cookie,
                       json={"CardCode": "CAC-10001", "CardName": "Test"}).status_code == 201
    assert client.patch("/sapb1/b1s/v1/BusinessPartners('CAC-10001')", headers=cookie,
                        json={"Phone1": "123"}).status_code == 204
    assert client.get("/sapb1/b1s/v1/BusinessPartners('CAC-10001')", headers=cookie).json()["Phone1"] == "123"


def test_salesforce_token_and_query():
    token = client.post("/salesforce/services/oauth2/token",
                        data={"grant_type": "client_credentials", "client_id": "a", "client_secret": "b"}).json()["access_token"]
    body = client.get("/salesforce/services/data/v60.0/query",
                      params={"q": "SELECT Id FROM Account WHERE LastModifiedDate > 2026-10-09T00:00:00Z"},
                      headers={"Authorization": f"Bearer {token}"}).json()
    assert body["done"] is True and body["records"][0]["attributes"]["type"] == "Account"


def test_wms_csv_export():
    r = client.get("/wms/api/v1/inventory/export", headers={"x-api-key": "mock-wms-key"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    header = r.text.splitlines()[0]
    assert header == "sku,warehouse_code,on_hand,allocated,damaged,updated_at"
    assert len(r.text.splitlines()) == 1 + 2 * len(mock_app.CATALOG)
