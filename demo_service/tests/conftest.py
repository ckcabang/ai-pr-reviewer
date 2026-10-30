import pytest
from fastapi.testclient import TestClient

from app import db, settings


@pytest.fixture(autouse=True)
def database(tmp_path, monkeypatch):
    """Every test gets a fresh SQLite file and fake credentials."""
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("EXPORT_DIR", str(tmp_path / "exports"))
    monkeypatch.setenv("PAYMENT_GATEWAY_API_KEY", "test-gateway-key")
    monkeypatch.setenv("WEBHOOK_SECRET", "test-webhook-secret")
    settings.get_settings.cache_clear()
    db.init_db()
    yield
    settings.get_settings.cache_clear()


@pytest.fixture
def client():
    from app.main import create_app

    return TestClient(create_app())


@pytest.fixture
def create_user():
    from app import auth

    def make(email="ann@example.com", password="correct-horse", *, admin=False):
        cur = db.execute(
            "INSERT INTO users (email, full_name, password_hash, is_admin) VALUES (?, ?, ?, ?)",
            (email, "Test User", auth.hash_password(password), int(admin)),
        )
        return cur.lastrowid

    return make


@pytest.fixture
def auth_headers():
    from app import auth

    return lambda user_id: {"Authorization": f"Bearer {auth.create_session(user_id)}"}
