"""Public product endpoints — catalog list + detail.

Detail endpoint preserves the bot URL contract (PRD §7.2):
- Published / unavailable / archived slugs all return 200.
- Truly unknown slugs return 404 via NotFoundError.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Path, Query

from app.core.pagination import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    Page,
)
from app.db.deps import DbSession
from app.schemas.product import ProductListItem, ProductRead
from app.services.catalog_service import CatalogService

router = APIRouter()


@router.get(
    "",
    response_model=Page[ProductListItem],
    summary="List published products with filters",
)
async def list_products(
    session: DbSession,
    q: Annotated[str | None, Query(max_length=200, description="Full-text search")] = None,
    category: Annotated[list[str] | None, Query(description="Repeat for multi-category OR")] = None,
    min_price: Annotated[Decimal | None, Query(ge=0)] = None,
    max_price: Annotated[Decimal | None, Query(ge=0)] = None,
    sort: Annotated[
        str,
        Query(pattern=r"^(new|price_asc|price_desc|bestseller|featured)$"),
    ] = "new",
    page: Annotated[int, Query(ge=1, le=10_000)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
) -> Page[ProductListItem]:
    items, total = await CatalogService(session).list_products(
        q=q,
        category_slugs=category,
        min_price=min_price,
        max_price=max_price,
        sort=sort,
        page=page,
        page_size=page_size,
    )
    return Page[ProductListItem](items=items, next_cursor=None, total=total)


@router.get(
    "/{slug}",
    response_model=ProductRead,
    summary="Product detail by slug — stable for bot URLs",
)
async def get_product(
    session: DbSession,
    slug: Annotated[str, Path(min_length=1, max_length=220)],
) -> ProductRead:
    return await CatalogService(session).get_product(slug)
