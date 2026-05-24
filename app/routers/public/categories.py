"""Public category endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from app.db.deps import DbSession
from app.schemas.category import CategoryGroup
from app.services.catalog_service import CatalogService

router = APIRouter()


@router.get(
    "",
    response_model=CategoryGroup,
    summary="List active categories grouped by kind",
)
async def list_categories(session: DbSession) -> CategoryGroup:
    return await CatalogService(session).list_categories_grouped()
