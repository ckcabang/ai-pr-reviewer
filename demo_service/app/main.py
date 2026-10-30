from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import db
from app.logging_setup import configure_logging
from app.routes import exports, inventory, orders, payments, refunds, users, webhooks


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    db.init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="Orders & Payments", lifespan=lifespan)
    for module in (orders, payments, refunds, users, inventory, webhooks, exports):
        app.include_router(module.router)
    return app


app = create_app()
