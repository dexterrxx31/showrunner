"""Asset catalog endpoints: list assets and set programming metadata.

Metadata (genre/rating/year) is what lets the AI director honour briefs like
"90s action, family-friendly until 21:00"; set it here after ingest.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.db import session_scope
from app.models import AssetRow

router = APIRouter()


class AssetPatch(BaseModel):
    genre: str | None = None
    rating: str | None = None
    year: int | None = None


def _serialize(row: AssetRow) -> dict:
    return {
        "asset_id": row.id,
        "title": row.title,
        "duration_seconds": round(sum(s.duration for s in row.segments), 1),
        "genre": row.genre,
        "rating": row.rating,
        "year": row.year,
    }


@router.get("/assets")
def list_assets() -> list[dict]:
    with session_scope() as s:
        rows = s.query(AssetRow).order_by(AssetRow.position, AssetRow.created_at).all()
        return [_serialize(r) for r in rows]


@router.patch("/assets/{asset_id}")
def patch_asset(asset_id: str, body: AssetPatch) -> dict:
    with session_scope() as s:
        row = s.get(AssetRow, asset_id)
        if row is None:
            raise HTTPException(status_code=404, detail="unknown asset")
        if body.genre is not None:
            row.genre = body.genre
        if body.rating is not None:
            row.rating = body.rating
        if body.year is not None:
            row.year = body.year
        return _serialize(row)
