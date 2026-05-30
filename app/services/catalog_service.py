"""CatalogService — orchestrates the customer-facing product/category reads.

Critical responsibility: enforce the bot URL contract (PRD §7.2):
- `get_product` returns even ARCHIVED products with `available=false`.
- Pure unknown slugs raise NotFoundError → 404.
"""

from __future__ import annotations

from decimal import Decimal

from app.core.exceptions import NotFoundError
from app.models.enums import CategoryKind, ProductStatus
from app.models.product import Product
from app.models.product_image import ProductImage
from app.models.product_variant import ProductVariant
from app.repositories.category_repo import CategoryRepository
from app.repositories.product_repo import ProductRepository
from app.schemas.category import CategoryGroup, CategorySummary
from app.schemas.product import (
    ImageRead,
    ProductListItem,
    ProductRead,
    VariantSummary,
)
from app.services.base import BaseService


class CatalogService(BaseService):
    @property
    def products(self) -> ProductRepository:
        return ProductRepository(self.session)

    @property
    def categories(self) -> CategoryRepository:
        return CategoryRepository(self.session)

    # ---------- Categories ----------

    async def list_categories_grouped(self) -> CategoryGroup:
        rows = await self.categories.list_active()
        grouped: dict[CategoryKind, list[CategorySummary]] = {
            kind: [] for kind in CategoryKind
        }
        for cat in rows:
            grouped[cat.kind].append(CategorySummary.model_validate(cat))
        return CategoryGroup(
            fabric=grouped[CategoryKind.FABRIC],
            occasion=grouped[CategoryKind.OCCASION],
            region=grouped[CategoryKind.REGION],
            price_bracket=grouped[CategoryKind.PRICE_BRACKET],
            color=grouped[CategoryKind.COLOR],
            collection=grouped[CategoryKind.COLLECTION],
        )

    # ---------- Products ----------

    async def list_products(
        self,
        *,
        q: str | None,
        category_slugs: list[str] | None,
        min_price: Decimal | None,
        max_price: Decimal | None,
        sort: str,
        page: int,
        page_size: int,
    ) -> tuple[list[ProductListItem], int]:
        rows, total = await self.products.list_catalog(
            q=q,
            category_slugs=category_slugs,
            min_price=min_price,
            max_price=max_price,
            sort=sort,
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        items = [self._to_list_item(p) for p in rows]
        return items, total

    async def get_product(self, slug: str) -> ProductRead:
        product = await self.products.get_by_slug_full(slug)
        if product is None:
            # Truly unknown slug — never existed → 404.
            raise NotFoundError(f"Product '{slug}' not found")
        return self._to_detail(product)

    # ---------- Shape helpers ----------

    @staticmethod
    def _primary_image(product: Product) -> ProductImage | None:
        for img in product.images:
            if img.is_primary:
                return img
        return product.images[0] if product.images else None

    @classmethod
    def _is_available(cls, product: Product) -> bool:
        if product.status == ProductStatus.PUBLISHED:
            return any(
                (v.inventory.stock - v.inventory.reserved) > 0
                for v in product.variants
                if v.is_active and v.inventory is not None
            )
        return False

    @classmethod
    def _to_list_item(cls, product: Product) -> ProductListItem:
        img = cls._primary_image(product)
        return ProductListItem(
            id=product.id,
            slug=product.slug,
            name=product.name,
            base_price=product.base_price,
            mrp=product.mrp,
            currency=product.currency,
            primary_image_url=img.url if img else None,
            available=product.status
            in (ProductStatus.PUBLISHED, ProductStatus.UNAVAILABLE),
            featured=product.featured,
            is_bestseller=product.is_bestseller,
            is_new=product.is_new,
        )

    @classmethod
    def _to_detail(cls, product: Product) -> ProductRead:
        return ProductRead(
            id=product.id,
            slug=product.slug,
            name=product.name,
            code=product.code,
            description=product.description,
            short_description=product.short_description,
            base_price=product.base_price,
            mrp=product.mrp,
            currency=product.currency,
            status=product.status,
            available=cls._is_available(product),
            tags=list(product.tags or []),
            featured=product.featured,
            is_bestseller=product.is_bestseller,
            is_new=product.is_new,
            seo_title=product.seo_title,
            seo_description=product.seo_description,
            published_at=product.published_at,
            created_at=product.created_at,
            updated_at=product.updated_at,
            images=[ImageRead.model_validate(i) for i in product.images],
            variants=[cls._variant_summary(v) for v in product.variants if v.is_active],
            categories=[
                CategorySummary.model_validate(link.category)
                for link in product.category_links
            ],
        )

    @staticmethod
    def _variant_summary(variant: ProductVariant) -> VariantSummary:
        price = variant.price_override or variant.product.base_price
        available = (
            variant.inventory is not None
            and (variant.inventory.stock - variant.inventory.reserved) > 0
        )
        return VariantSummary(
            id=variant.id,
            sku=variant.sku,
            color=variant.color,
            fabric=variant.fabric,
            size=variant.size,
            price=price,
            available=available,
            stock=None,  # Admin variant API will fill this when caller is admin
        )
