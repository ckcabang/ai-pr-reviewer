import pytest

from app import db
from app.services import order_state


@pytest.mark.parametrize(
    "current,new",
    [("pending", "paid"), ("pending", "cancelled"), ("paid", "shipped"), ("paid", "refunded"),
     ("shipped", "delivered"), ("delivered", "refunded")],
)
def test_allowed_transitions(current, new):
    assert order_state.is_allowed(current, new)


@pytest.mark.parametrize(
    "current,new",
    [("pending", "refunded"), ("pending", "shipped"), ("cancelled", "paid"),
     ("refunded", "paid"), ("delivered", "pending")],
)
def test_forbidden_transitions(current, new):
    assert not order_state.is_allowed(current, new)


def _seed_pending_order():
    db.execute("INSERT INTO users (id, email, full_name, password_hash) VALUES (1, 'a@b.c', 'A', 'x')")
    db.execute("INSERT INTO orders (id, user_id, status) VALUES (1, 1, 'pending')")


def test_transition_updates_the_order_row():
    _seed_pending_order()
    order = {"id": 1, "status": "pending"}

    order_state.transition(order, "paid")

    assert db.execute("SELECT status FROM orders WHERE id = 1").one()["status"] == "paid"
    assert order["status"] == "paid"


def test_transition_rejects_illegal_move_and_leaves_row_untouched():
    _seed_pending_order()

    with pytest.raises(order_state.InvalidTransition):
        order_state.transition({"id": 1, "status": "pending"}, "refunded")

    assert db.execute("SELECT status FROM orders WHERE id = 1").one()["status"] == "pending"
