from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app import auth
from app.models import CurrentUser
from app.services import orders
from app.settings import get_settings

router = APIRouter(prefix="/exports", tags=["exports"])


@router.get("/orders")
def export_orders(user: CurrentUser = Depends(auth.get_current_user)):
    """Write the caller's orders to a JSON file in the export directory and return it."""
    export_dir = Path(get_settings().export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / f"orders_{user.id}.json"  # name is derived from the user id, never from input
    rows = [orders.to_out(o).model_dump(mode="json") for o in orders.list_orders(user.id)]
    path.write_text(json.dumps(rows, indent=2))
    return FileResponse(path, media_type="application/json", filename=path.name)
