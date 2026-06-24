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
from app.repositories.reservation_repo import ReservationRepository
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
        # DERIVED availability: stock - SUM(active reservations). The stored
        # inventory.reserved column is deprecated and NOT consulted (Model A).
        variant_ids = [v.id for v in product.variants if v.is_active]
        reserved_map = await ReservationRepository(
            self.session
        ).active_reserved_for_variants(variant_ids)
        return self._to_detail(product, reserved_map)

    # ---------- Shape helpers ----------

    @staticmethod
    def _primary_image(product: Product) -> ProductImage | None:
        for img in product.images:
            if img.is_primary:
                return img
        return product.images[0] if product.images else None

    @staticmethod
    def _available_quantity(variant: ProductVariant, reserved: int) -> int:
        inv = variant.inventory
        if inv is None:
            return 0
        return max(inv.stock - reserved, 0)

    @staticmethod
    def _availability_state(
        *, buyable: bool, stock: int, available_qty: int
    ) -> str:
        """The six DB-driven storefront states (see FRONTEND messaging rules)."""
        if not buyable or stock <= 0:
            return "out_of_stock"
        if available_qty <= 0:
            return "reserved"  # physical stock exists but all currently held
        if available_qty == 1:
            return "last_one"
        if available_qty <= 5:
            return "selling_fast"
        if available_qty <= 10:
            return "low"
        return "in_stock"

    def _is_available(
        self, product: Product, reserved_map: dict
    ) -> bool:
        if product.status != ProductStatus.PUBLISHED:
            return False
        return any(
            self._available_quantity(v, reserved_map.get(v.id, 0)) > 0
            for v in product.variants
            if v.is_active and v.inventory is not None
        )

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

    def _to_detail(self, product: Product, reserved_map: dict) -> ProductRead:
        published = product.status == ProductStatus.PUBLISHED
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
            available=self._is_available(product, reserved_map),
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
            variants=[
                self._variant_summary(
                    v, reserved_map.get(v.id, 0), product_published=published
                )
                for v in product.variants
                if v.is_active
            ],
            categories=[
                CategorySummary.model_validate(link.category)
                for link in product.category_links
            ],
        )

    def _variant_summary(
        self, variant: ProductVariant, reserved: int, *, product_published: bool
    ) -> VariantSummary:
        price = variant.price_override or variant.product.base_price
        stock = variant.inventory.stock if variant.inventory is not None else 0
        available_qty = self._available_quantity(variant, reserved)
        buyable = product_published and variant.is_active
        state = self._availability_state(
            buyable=buyable, stock=stock, available_qty=available_qty
        )
        return VariantSummary(
            id=variant.id,
            sku=variant.sku,
            color=variant.color,
            fabric=variant.fabric,
            size=variant.size,
            price=price,
            available=buyable and available_qty > 0,
            # Exact remaining units are NOT disclosed publicly — `available`
            # (boolean) + `availability_state` (bucket) carry the storefront
            # signal; `available_qty` is still used above/below to derive them.
            available_quantity=None,
            availability_state=state,
            stock=None,  # Admin variant API will fill this when caller is admin
        )
