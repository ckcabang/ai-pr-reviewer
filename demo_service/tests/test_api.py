import json
from decimal import Decimal

import httpx
import pytest

from app import db
from app.clients import email_client, payment_gateway
from app.routes import webhooks


@pytest.fixture
def stocked():
    db.execute("INSERT INTO inventory (sku, name, price, stock) VALUES ('W-1', 'Widget', '10.00', 5)")


@pytest.fixture
def gateway():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        if request.url.path.endswith("/refunds"):
            return httpx.Response(200, json={"id": "re_1"})
        return httpx.Response(200, json={"id": "ch_1"})

    payment_gateway._transport = httpx.MockTransport(handler)
    email_client._transport = httpx.MockTransport(lambda r: httpx.Response(202, json={}))
    yield calls
    payment_gateway._transport = None
    email_client._transport = None


def _place_order(client, headers, qty=2):
    resp = client.post("/orders", json={"items": [{"sku": "W-1", "quantity": qty}]}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_orders_require_authentication(client):
    assert client.get("/orders").status_code == 401


def test_user_cannot_read_another_users_order(client, stocked, create_user, auth_headers):
    ann, bob = create_user("ann@example.com"), create_user("bob@example.com")
    order = _place_order(client, auth_headers(ann))
    assert client.get(f"/orders/{order['id']}", headers=auth_headers(bob)).status_code == 403
    assert client.get(f"/orders/{order['id']}", headers=auth_headers(ann)).status_code == 200


def test_creating_an_order_reserves_stock_and_prices_it(client, stocked, create_user, auth_headers):
    user = create_user()
    order = _place_order(client, auth_headers(user), qty=2)
    assert Decimal(order["total"]) == Decimal("21.65")  # 20.00 + 8.25% tax
    assert db.execute("SELECT stock FROM inventory WHERE sku = 'W-1'").one()["stock"] == 3


def test_ordering_more_than_stock_is_rejected_and_stock_untouched(client, stocked, create_user, auth_headers):
    user = create_user()
    resp = client.post("/orders", json={"items": [{"sku": "W-1", "quantity": 6}]}, headers=auth_headers(user))
    assert resp.status_code == 409
    assert db.execute("SELECT stock FROM inventory WHERE sku = 'W-1'").one()["stock"] == 5


def test_invoice_amount_due_matches_order_total(client, stocked, create_user, auth_headers):
    user = create_user()
    order = _place_order(client, auth_headers(user), qty=2)
    invoice = client.get(f"/orders/{order['id']}/invoice", headers=auth_headers(user)).json()
    assert Decimal(invoice["amount_due"]) == Decimal(order["total"])
    assert invoice["display_total"] == "$21.65"


def test_capture_requires_an_idempotency_key(client, stocked, gateway, create_user, auth_headers):
    user = create_user()
    order = _place_order(client, auth_headers(user))
    resp = client.post("/payments/capture", json={"order_id": order["id"]}, headers=auth_headers(user))
    assert resp.status_code == 422


def test_capture_charges_once_and_replays_on_the_same_key(client, stocked, gateway, create_user, auth_headers):
    user = create_user()
    headers = {**auth_headers(user), "Idempotency-Key": "key-1"}
    order = _place_order(client, headers)

    first = client.post("/payments/capture", json={"order_id": order["id"]}, headers=headers)
    second = client.post("/payments/capture", json={"order_id": order["id"]}, headers=headers)

    assert first.status_code == 200 and second.json() == first.json()
    assert gateway.count(("POST", "/charges")) == 1
    assert client.get(f"/orders/{order['id']}", headers=headers).json()["status"] == "paid"


def test_failed_capture_returns_generic_error_and_frees_the_key(client, stocked, gateway, create_user, auth_headers):
    user = create_user()
    headers = {**auth_headers(user), "Idempotency-Key": "key-2"}
    order = _place_order(client, headers)
    payment_gateway._transport = httpx.MockTransport(lambda r: httpx.Response(500, text="boom: secret detail"))

    resp = client.post("/payments/capture", json={"order_id": order["id"]}, headers=headers)

    assert resp.status_code == 502 and "boom" not in resp.text
    assert db.execute("SELECT COUNT(*) AS n FROM idempotency_keys").one()["n"] == 0


def test_refund_of_pending_order_is_rejected(client, stocked, gateway, create_user, auth_headers):
    user = create_user()
    headers = {**auth_headers(user), "Idempotency-Key": "key-3"}
    order = _place_order(client, headers)
    resp = client.post("/refunds", json={"order_id": order["id"]}, headers=headers)
    assert resp.status_code == 409


def test_refund_after_capture_marks_order_refunded(client, stocked, gateway, create_user, auth_headers):
    user = create_user()
    order = _place_order(client, auth_headers(user))
    client.post("/payments/capture", json={"order_id": order["id"]},
                headers={**auth_headers(user), "Idempotency-Key": "cap"})

    resp = client.post("/refunds", json={"order_id": order["id"]},
                       headers={**auth_headers(user), "Idempotency-Key": "ref"})

    assert resp.status_code == 200 and resp.json()["status"] == "refunded"
    assert client.get(f"/orders/{order['id']}", headers=auth_headers(user)).json()["status"] == "refunded"


def test_shipping_webhook_rejects_bad_signature_and_applies_good_one(client, stocked, create_user, auth_headers):
    user = create_user()
    order = _place_order(client, auth_headers(user))
    db.execute("UPDATE orders SET status = 'paid' WHERE id = ?", (order["id"],))
    body = json.dumps({"order_id": order["id"], "status": "shipped"}).encode()

    bad = client.post("/webhooks/shipping", content=body, headers={"X-Signature": "nope"})
    good = client.post("/webhooks/shipping", content=body,
                       headers={"X-Signature": webhooks.sign(body, "test-webhook-secret")})

    assert bad.status_code == 401
    assert good.status_code == 200 and good.json()["status"] == "shipped"


def test_inventory_restock_is_admin_only(client, stocked, create_user, auth_headers):
    member, admin = create_user("m@example.com"), create_user("a@example.com", admin=True)
    url = "/inventory/W-1/restock"
    assert client.post(url, json={"quantity": 3}, headers=auth_headers(member)).status_code == 403
    resp = client.post(url, json={"quantity": 3}, headers=auth_headers(admin))
    assert resp.status_code == 200 and resp.json()["stock"] == 8


def test_login_and_profile_flow(client):
    client.post("/users", json={"email": "new@example.com", "full_name": "New", "password": "long-enough-pw"})
    assert client.post("/users/login", json={"email": "new@example.com", "password": "wrong-password"}).status_code == 401
    token = client.post("/users/login", json={"email": "new@example.com", "password": "long-enough-pw"}).json()["token"]
    me = db.execute("SELECT id FROM users WHERE email = 'new@example.com'").one()["id"]
    resp = client.get(f"/users/{me}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["email"] == "new@example.com"


def test_export_writes_a_file_named_from_the_user_id(client, stocked, tmp_path, create_user, auth_headers):
    user = create_user()
    _place_order(client, auth_headers(user))
    resp = client.get("/exports/orders", headers=auth_headers(user))
    assert resp.status_code == 200 and len(resp.json()) == 1
    assert (tmp_path / "exports" / f"orders_{user}.json").exists()
