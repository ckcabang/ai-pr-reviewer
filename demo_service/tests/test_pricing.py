from decimal import Decimal

import pytest

from app import db, settings
from app.services import pricing


def test_apply_discount_takes_percentage_off():
    assert pricing.apply_discount(Decimal("200.00"), Decimal("25")) == Decimal("150.00")


def test_apply_discount_is_clamped_to_zero_total():
    assert pricing.apply_discount(Decimal("40.00"), Decimal("150")) == Decimal("0.00")


def test_calculate_tax_rounds_half_up_to_cents():
    assert pricing.calculate_tax(Decimal("19.99"), Decimal("0.0825")) == Decimal("1.65")


def test_money_stays_decimal():
    total = pricing.apply_discount(Decimal("0.10") + Decimal("0.20"), Decimal("0"))
    assert isinstance(total, Decimal)
    assert total == Decimal("0.30")


def _seed_order(discount_code=None):
    db.execute("INSERT INTO users (id, email, full_name, password_hash) VALUES (1, 'a@b.c', 'A', 'x')")
    db.execute("INSERT INTO orders (id, user_id, status, discount_code) VALUES (1, 1, 'pending', ?)", (discount_code,))
    db.execute("INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (1, 'W-1', 2, '10.00')")
    db.execute("INSERT INTO order_items (order_id, sku, quantity, unit_price) VALUES (1, 'W-2', 1, '5.50')")


def _set_tax_rate(monkeypatch, rate):
    monkeypatch.setenv("TAX_RATE", rate)
    settings.get_settings.cache_clear()


def test_order_total_is_decimal_dollars_with_tax(monkeypatch):
    _set_tax_rate(monkeypatch, "0.10")
    _seed_order()
    # subtotal 25.50 + 10% tax 2.55
    assert pricing.get_order_total(1) == Decimal("28.05")


def test_order_total_applies_discount_code_before_tax(monkeypatch):
    _set_tax_rate(monkeypatch, "0.10")
    db.execute("INSERT INTO discount_codes (code, percent_off) VALUES ('SAVE20', '20')")
    _seed_order("SAVE20")
    # 25.50 - 20% = 20.40, + 10% tax 2.04
    assert pricing.get_order_total(1) == Decimal("22.44")


def test_unknown_discount_code_is_rejected():
    _seed_order("NOPE")
    with pytest.raises(pricing.UnknownDiscountCode):
        pricing.get_order_total(1)
