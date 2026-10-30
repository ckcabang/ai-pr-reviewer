from decimal import Decimal

import httpx
import pytest

from app.clients import payment_gateway


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(payment_gateway, "RETRY_DELAY_SECONDS", 0)
    yield
    payment_gateway._transport = None


def test_capture_is_not_retried_after_a_failure():
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        return httpx.Response(503)

    payment_gateway._transport = httpx.MockTransport(handler)
    with pytest.raises(payment_gateway.GatewayError):
        payment_gateway.capture(1, Decimal("10.00"))
    assert len(attempts) == 1


def test_reads_are_retried_until_they_succeed():
    responses = [httpx.Response(503), httpx.Response(503), httpx.Response(200, json={"id": "ch_9"})]
    payment_gateway._transport = httpx.MockTransport(lambda request: responses.pop(0))
    assert payment_gateway.get_charge("ch_9") == {"id": "ch_9"}
    assert responses == []
