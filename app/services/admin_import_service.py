"""AdminImportService — orchestrates CSV bulk imports.

Architecture:
  1. HTTP handler creates an `import_jobs` row (status=queued) and
     dispatches `run_import_job` via `BackgroundTasks`. Response goes
     back to the admin immediately with the job_id.
  2. The background task opens a FRESH DB session (the request session
     has closed by then) and runs `process_csv` against the row.
  3. `process_csv` streams the CSV header-aware, batches rows into
     chunks (default 100), validates each chunk via Pydantic, runs the
     kind-specific persistence step, commits per chunk.

Chunked commits give partial-failure tolerance: if chunk 17 of 50 dies,
chunks 1..16 are durable. The job row records the failure reason; admin
can re-upload a corrected file for the surviving rows.

Dry-run mode validates + reports without persisting. Useful for catching
schema mistakes before risking a half-applied batch.

Errors per row are capped at `MAX_RECORDED_ERRORS` so a runaway CSV
can't bloat the job row beyond ~500KB.
"""

from __future__ import annotations

import csv
import io
import uuid
from collections.abc import AsyncIterator, Iterable
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.pagination import OffsetParams, Page, paginate_offset
from app.db.session import session_scope
from app.models.category import ProductCategory
from app.models.enums import (
    AuditAction,
    ImportKind,
    ImportStatus,
    StockMovementReason,
)
from app.models.import_job import ImportJob
from app.models.inventory import Inventory, StockMovement
from app.models.product import Product
from app.models.product_variant import ProductVariant
from app.schemas.admin_import import (
    ImportJobListItem,
    ImportJobRead,
    InventoryCsvRow,
    ProductCsvRow,
    VariantCsvRow,
)
from app.services.audit_service import AuditService
from app.services.base import BaseService
from app.services.inventory_service import InventoryService

log = get_logger(__name__)


# Tuning knobs. Hard-coded constants — admin doesn't need to fiddle with
# these per-request and they're tested as a unit.
DEFAULT_CHUNK_SIZE = 100
MAX_CHUNK_SIZE = 500
MAX_RECORDED_ERRORS = 1000
MAX_CSV_BYTES = 50 * 1024 * 1024  # 50 MB — bounded for safety
MAX_RAW_FIELDS_IN_ERROR = 10  # truncate `raw` payload in error rows


# Pydantic row classes per kind.
_ROW_SCHEMA_BY_KIND = {
    ImportKind.PRODUCT: ProductCsvRow,
    ImportKind.VARIANT: VariantCsvRow,
    ImportKind.INVENTORY: InventoryCsvRow,
}


# =============================================================================
# Public service — admin endpoints land here
# =============================================================================


class AdminImportService(BaseService):
    @property
    def audit(self) -> AuditService:
        return AuditService(self.session)

    # ---------- Reads ---------------------------------------------------

    async def list_jobs(
        self,
        *,
        params: OffsetParams,
        kind: ImportKind | None,
        status: ImportStatus | None,
    ) -> Page[ImportJobListItem]:
        stmt = select(ImportJob)
        if kind is not None:
            stmt = stmt.where(ImportJob.kind == kind)
        if status is not None:
            stmt = stmt.where(ImportJob.status == status)
        stmt = stmt.order_by(ImportJob.created_at.desc(), ImportJob.id.desc())
        rows, total = await paginate_offset(self.session, stmt, params)
        items = [ImportJobListItem.model_validate(r) for r in rows]
        return Page[ImportJobListItem](items=items, total=total)

    async def get_job(self, job_id: uuid.UUID) -> ImportJobRead:
        job = await self.session.get(ImportJob, job_id)
        if job is None:
            raise NotFoundError("Import job not found")
        return ImportJobRead.model_validate(job)

    # ---------- Create + dispatch ---------------------------------------

    async def create_job(
        self,
        *,
        kind: ImportKind,
        csv_bytes: bytes,
        filename: str | None,
        dry_run: bool,
        actor_user_id: uuid.UUID,
        request_id: str | None,
    ) -> ImportJob:
        """Insert the job row + audit log. The HTTP layer then schedules
        `run_import_job` via BackgroundTasks against this row's id."""
        if len(csv_bytes) > MAX_CSV_BYTES:
            raise ValidationError(
                f"CSV exceeds {MAX_CSV_BYTES // 1024 // 1024} MB limit",
                code="csv_too_large",
                extra={
                    "max_bytes": MAX_CSV_BYTES,
                    "received_bytes": len(csv_bytes),
                },
            )
        if not csv_bytes.strip():
            raise ValidationError(
                "CSV body is empty", code="csv_empty",
            )

        job = ImportJob(
            kind=kind,
            status=ImportStatus.QUEUED,
            filename=filename,
            dry_run=dry_run,
            metadata_={"chunk_size": DEFAULT_CHUNK_SIZE},
            created_by=actor_user_id,
            request_id=request_id,
        )
        self.session.add(job)
        await self.session.flush()

        await self.audit.record(
            action=AuditAction.CREATE,
            entity_type="import_job",
            entity_id=job.id,
            actor_user_id=actor_user_id,
            summary=(
                f"Queued {kind.value} CSV import"
                + (" (dry-run)" if dry_run else "")
                + f" — {len(csv_bytes)} bytes"
            ),
            metadata={
                "kind": kind.value,
                "dry_run": dry_run,
                "filename": filename,
                "bytes": len(csv_bytes),
            },
            request_id=request_id,
        )
        return job


# =============================================================================
# BackgroundTask entrypoint
# =============================================================================


async def run_import_job(
    job_id: uuid.UUID,
    csv_bytes: bytes,
    kind: ImportKind,
    *,
    dry_run: bool,
    actor_user_id: uuid.UUID,
) -> None:
    """Background-task entrypoint. Opens its own session — the request
    that scheduled this task has long since returned.

    Catches every exception so a crash inside the processor doesn't
    leave the job stuck in `running`. Catastrophic failures land in
    `failure_reason`; partial commits up to that point are preserved.
    """
    async for session in session_scope():
        try:
            await _process(
                session=session,
                job_id=job_id,
                csv_bytes=csv_bytes,
                kind=kind,
                dry_run=dry_run,
                actor_user_id=actor_user_id,
            )
        except Exception as exc:
            # The processor's per-chunk try/except already captures
            # row-level failures into `errors`. Anything reaching here
            # is catastrophic (CSV format, DB connection, etc.).
            log.exception(
                "import_job_catastrophic_failure",
                job_id=str(job_id),
                kind=kind.value,
            )
            await _mark_failed(session, job_id, str(exc))
            await session.commit()


async def _mark_failed(
    session, job_id: uuid.UUID, reason: str,  # type: ignore[no-untyped-def]
) -> None:
    job = await session.get(ImportJob, job_id)
    if job is None:
        return
    job.status = ImportStatus.FAILED
    job.failure_reason = reason[:2000]
    job.completed_at = datetime.now(UTC)


# =============================================================================
# Processor — orchestrates a single job's lifecycle
# =============================================================================


async def _process(
    *,
    session,  # AsyncSession  # type: ignore[no-untyped-def]
    job_id: uuid.UUID,
    csv_bytes: bytes,
    kind: ImportKind,
    dry_run: bool,
    actor_user_id: uuid.UUID,
) -> None:
    """Walk the CSV, validate + persist in chunks. Commit per chunk."""
    job = await session.get(ImportJob, job_id)
    if job is None:
        log.error("import_job_not_found", job_id=str(job_id))
        return

    job.status = ImportStatus.RUNNING
    job.started_at = datetime.now(UTC)
    await session.commit()

    # csv reader needs a str stream; decode once. The body cap was
    # enforced at `create_job`; here we trust the bytes.
    try:
        text = csv_bytes.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = csv_bytes.decode("latin-1")

    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        await _mark_failed(session, job_id, "CSV has no header row")
        await session.commit()
        return

    row_schema = _ROW_SCHEMA_BY_KIND[kind]
    chunk_size = DEFAULT_CHUNK_SIZE
    errors_recorded: list[dict[str, Any]] = []
    rows_processed = 0
    rows_succeeded = 0
    rows_failed = 0

    async for chunk_index, chunk in _chunked_rows(reader, chunk_size):
        # Parse + validate each row in the chunk. Bad rows go into
        # `chunk_errors`; valid rows go into `valid_rows`.
        valid_rows: list[tuple[int, Any, dict]] = []  # (row_number, model, raw)
        chunk_errors: list[dict[str, Any]] = []

        for row_offset, raw in enumerate(chunk):
            # +2 because: header is line 1, data lines start at line 2,
            # and `row_offset` is 0-indexed within the chunk.
            row_number = chunk_index * chunk_size + row_offset + 2
            try:
                model = row_schema.model_validate(raw)
                valid_rows.append((row_number, model, raw))
            except PydanticValidationError as exc:
                chunk_errors.append(
                    _error_payload(row_number, exc.errors(), raw)
                )

        # Persist the valid rows. Each kind has its own apply step.
        if valid_rows and not dry_run:
            applied_ok, persist_errors = await _apply_chunk(
                session=session,
                kind=kind,
                rows=valid_rows,
                actor_user_id=actor_user_id,
            )
            rows_succeeded += applied_ok
            chunk_errors.extend(persist_errors)
            try:
                await session.commit()
            except IntegrityError as exc:
                # A chunk-level commit failure means some row violated a
                # DB constraint that the per-row check missed. Roll back
                # the chunk; report the failure; continue.
                await session.rollback()
                chunk_errors.append({
                    "row_number": valid_rows[0][0],
                    "errors": {"_chunk": str(exc.orig)[:500]},
                    "raw": {},
                })
                # On rollback, the failed rows in this chunk are NOT
                # counted as succeeded — adjust the tally.
                rows_succeeded -= applied_ok
                rows_failed += len(valid_rows)
        elif valid_rows and dry_run:
            # Dry-run: count valid rows but don't persist.
            rows_succeeded += len(valid_rows)

        rows_failed += len(chunk_errors)

        # Cap the stored errors so a 100k-row failure can't blow the
        # JSONB column up to MB-scale.
        for err in chunk_errors:
            if len(errors_recorded) < MAX_RECORDED_ERRORS:
                errors_recorded.append(err)
            else:
                break
        rows_processed += len(chunk)

        # Update the job row progress in its own tx so the admin's poll
        # can watch the bar advance.
        await _update_progress(
            session,
            job_id,
            rows_total=None,  # unknown until reader exhausts
            rows_processed=rows_processed,
            rows_succeeded=rows_succeeded,
            rows_failed=rows_failed,
            errors=errors_recorded,
        )

    # Final summary commit.
    job = await session.get(ImportJob, job_id)
    if job is None:
        return
    job.rows_total = rows_processed
    job.rows_processed = rows_processed
    job.rows_succeeded = rows_succeeded
    job.rows_failed = rows_failed
    job.errors = errors_recorded
    job.status = ImportStatus.COMPLETED
    job.completed_at = datetime.now(UTC)
    job.summary = (
        f"{rows_succeeded} succeeded / {rows_failed} failed / "
        f"{rows_processed} processed"
        + (" (dry-run — no rows persisted)" if dry_run else "")
    )
    await session.commit()

    log.info(
        "import_job_completed",
        job_id=str(job_id),
        kind=kind.value,
        rows_processed=rows_processed,
        rows_succeeded=rows_succeeded,
        rows_failed=rows_failed,
        dry_run=dry_run,
    )


async def _update_progress(
    session,  # type: ignore[no-untyped-def]
    job_id: uuid.UUID,
    *,
    rows_total: int | None,
    rows_processed: int,
    rows_succeeded: int,
    rows_failed: int,
    errors: list[dict[str, Any]],
) -> None:
    job = await session.get(ImportJob, job_id)
    if job is None:
        return
    if rows_total is not None:
        job.rows_total = rows_total
    job.rows_processed = rows_processed
    job.rows_succeeded = rows_succeeded
    job.rows_failed = rows_failed
    job.errors = list(errors)
    await session.commit()


async def _chunked_rows(
    reader: csv.DictReader, chunk_size: int
) -> AsyncIterator[tuple[int, list[dict]]]:
    """Stream rows from the CSV in chunks of `chunk_size`.

    Async-generator wrapper so `_process` can await between chunks if we
    later add backpressure (e.g. periodic yields to other tasks).
    """
    buf: list[dict] = []
    chunk_index = 0
    for row in reader:
        buf.append(row)
        if len(buf) >= chunk_size:
            yield chunk_index, buf
            buf = []
            chunk_index += 1
    if buf:
        yield chunk_index, buf


def _error_payload(
    row_number: int, errors: Iterable[dict], raw: dict
) -> dict[str, Any]:
    """Compact, JSON-safe error record for one row.

    Strips Pydantic's `ctx` field (may contain raw Python exceptions).
    Caps the raw-data echo to `MAX_RAW_FIELDS_IN_ERROR` fields so a wide
    CSV doesn't bloat per-row errors.
    """
    cleaned_errors: dict[str, Any] = {}
    for err in errors:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        cleaned_errors[loc or "_root"] = err.get("msg", "invalid")
    raw_trimmed = dict(list(raw.items())[:MAX_RAW_FIELDS_IN_ERROR])
    return {
        "row_number": row_number,
        "errors": cleaned_errors,
        "raw": raw_trimmed,
    }


# =============================================================================
# Per-kind persistence — one function per ImportKind
# =============================================================================


async def _apply_chunk(
    *,
    session,  # type: ignore[no-untyped-def]
    kind: ImportKind,
    rows: list[tuple[int, Any, dict]],
    actor_user_id: uuid.UUID,
) -> tuple[int, list[dict[str, Any]]]:
    """Dispatch by kind. Returns `(applied_ok, row_errors)`."""
    if kind is ImportKind.PRODUCT:
        return await _apply_products(session, rows, actor_user_id)
    if kind is ImportKind.VARIANT:
        return await _apply_variants(session, rows, actor_user_id)
    if kind is ImportKind.INVENTORY:
        return await _apply_inventory(session, rows, actor_user_id)
    raise NotImplementedError(f"Unknown import kind: {kind}")


# ---------- Products ----------------------------------------------------


async def _apply_products(
    session,  # type: ignore[no-untyped-def]
    rows: list[tuple[int, ProductCsvRow, dict]],
    actor_user_id: uuid.UUID,
) -> tuple[int, list[dict[str, Any]]]:
    from app.services.admin_product_service import _slugify  # local import to avoid cycle

    applied = 0
    errors: list[dict[str, Any]] = []
    # Pre-check slug collisions within the chunk + against DB. Intra-chunk
    # duplicates are reported before INSERT to give a clear error.
    seen_slugs: set[str] = set()
    candidate_slugs: list[tuple[int, str, ProductCsvRow, dict]] = []
    for row_number, model, raw in rows:
        slug = (model.slug or _slugify(model.name)).lower()
        if slug in seen_slugs:
            errors.append({
                "row_number": row_number,
                "errors": {"slug": f"duplicate slug '{slug}' within this CSV chunk"},
                "raw": raw,
            })
            continue
        seen_slugs.add(slug)
        candidate_slugs.append((row_number, slug, model, raw))

    if not candidate_slugs:
        return applied, errors

    existing_stmt = select(Product.slug).where(
        Product.slug.in_([s for _, s, _, _ in candidate_slugs])
    )
    existing = {
        row[0] for row in (await session.execute(existing_stmt)).all()
    }

    for row_number, slug, model, raw in candidate_slugs:
        if slug in existing:
            errors.append({
                "row_number": row_number,
                "errors": {"slug": f"slug '{slug}' already exists in the catalog"},
                "raw": raw,
            })
            continue
        try:
            session.add(
                Product(
                    slug=slug,
                    name=model.name.strip(),
                    short_description=model.short_description,
                    description=model.description,
                    base_price=model.base_price,
                    mrp=model.mrp,
                    currency=model.currency.upper(),
                    status=model.status,
                    tags=list(model.tags),
                    featured=model.featured,
                    is_bestseller=model.is_bestseller,
                    is_new=model.is_new,
                    seo_title=model.seo_title,
                    seo_description=model.seo_description,
                    created_by=actor_user_id,
                    updated_by=actor_user_id,
                )
            )
            await session.flush()
            applied += 1
        except IntegrityError as exc:
            await session.rollback()
            errors.append({
                "row_number": row_number,
                "errors": {"_db": str(exc.orig)[:300]},
                "raw": raw,
            })
    _ = ProductCategory  # silence unused-import warning; categories not yet
    return applied, errors


# ---------- Variants ----------------------------------------------------


async def _apply_variants(
    session,  # type: ignore[no-untyped-def]
    rows: list[tuple[int, VariantCsvRow, dict]],
    actor_user_id: uuid.UUID,
) -> tuple[int, list[dict[str, Any]]]:
    applied = 0
    errors: list[dict[str, Any]] = []

    # Resolve product_slug → product_id in one query for the chunk.
    slugs = sorted({r[1].product_slug for r in rows})
    product_stmt = select(Product.id, Product.slug).where(Product.slug.in_(slugs))
    product_by_slug: dict[str, uuid.UUID] = {
        row[1]: row[0] for row in (await session.execute(product_stmt)).all()
    }

    # Intra-chunk SKU dup check.
    seen_skus: set[str] = set()
    for row_number, model, raw in rows:
        if model.sku in seen_skus:
            errors.append({
                "row_number": row_number,
                "errors": {"sku": f"duplicate SKU '{model.sku}' within this CSV chunk"},
                "raw": raw,
            })
            continue
        seen_skus.add(model.sku)

        product_id = product_by_slug.get(model.product_slug)
        if product_id is None:
            errors.append({
                "row_number": row_number,
                "errors": {
                    "product_slug": f"product slug '{model.product_slug}' not found",
                },
                "raw": raw,
            })
            continue

        try:
            variant = ProductVariant(
                product_id=product_id,
                sku=model.sku.strip(),
                color=model.color,
                fabric=model.fabric,
                size=model.size,
                price_override=model.price_override,
                weight_grams=model.weight_grams,
                is_default=model.is_default,
                is_active=model.is_active,
            )
            session.add(variant)
            await session.flush()
            session.add(
                Inventory(
                    variant_id=variant.id,
                    stock=model.initial_stock,
                    reserved=0,
                    low_stock_threshold=model.low_stock_threshold,
                )
            )
            await session.flush()
            applied += 1
        except IntegrityError as exc:
            await session.rollback()
            errors.append({
                "row_number": row_number,
                "errors": {"_db": str(exc.orig)[:300]},
                "raw": raw,
            })

    _ = actor_user_id  # AuditableMixin would consume this; variants are not auditable today
    return applied, errors


# ---------- Inventory ---------------------------------------------------


async def _apply_inventory(
    session,  # type: ignore[no-untyped-def]
    rows: list[tuple[int, InventoryCsvRow, dict]],
    actor_user_id: uuid.UUID,
) -> tuple[int, list[dict[str, Any]]]:
    applied = 0
    errors: list[dict[str, Any]] = []

    skus = sorted({r[1].sku for r in rows})
    variant_stmt = (
        select(ProductVariant.id, ProductVariant.sku, ProductVariant.product_id)
        .where(ProductVariant.sku.in_(skus))
    )
    variant_by_sku: dict[str, tuple[uuid.UUID, uuid.UUID]] = {
        sku: (vid, pid)
        for vid, sku, pid in (await session.execute(variant_stmt)).all()
    }

    if not variant_by_sku:
        # All rows will fail; report each one separately for clarity.
        for row_number, model, raw in rows:
            errors.append({
                "row_number": row_number,
                "errors": {"sku": f"variant '{model.sku}' not found"},
                "raw": raw,
            })
        return applied, errors

    inv_service = InventoryService(session)
    affected_product_ids: set[uuid.UUID] = set()

    for row_number, model, raw in rows:
        variant_entry = variant_by_sku.get(model.sku)
        if variant_entry is None:
            errors.append({
                "row_number": row_number,
                "errors": {"sku": f"variant '{model.sku}' not found"},
                "raw": raw,
            })
            continue
        variant_id, product_id = variant_entry

        locks = await inv_service.lock_inventory_rows([variant_id])
        inv = locks.get(variant_id)
        if inv is None:
            errors.append({
                "row_number": row_number,
                "errors": {"sku": f"inventory row for variant '{model.sku}' missing"},
                "raw": raw,
            })
            continue

        target = _compute_target(model.mode, model.value, inv.stock)
        if target < 0:
            errors.append({
                "row_number": row_number,
                "errors": {"value": "adjustment would push stock below zero"},
                "raw": raw,
            })
            continue
        if target < inv.reserved:
            errors.append({
                "row_number": row_number,
                "errors": {
                    "value": (
                        f"adjustment would push stock below reserved "
                        f"({target} < reserved {inv.reserved})"
                    ),
                },
                "raw": raw,
            })
            continue

        delta = target - inv.stock
        if delta == 0:
            applied += 1
            continue
        inv.stock = target
        session.add(
            StockMovement(
                variant_id=variant_id,
                delta=delta,
                reason=StockMovementReason(model.reason),
                actor_user_id=actor_user_id,
                note=model.note or f"CSV import (row {row_number})",
            )
        )
        affected_product_ids.add(product_id)
        applied += 1

    # After all rows in the chunk are applied, recompute availability
    # for affected products (PUBLISHED ↔ UNAVAILABLE flips).
    for pid in affected_product_ids:
        await inv_service.recompute_product_availability(
            pid, actor_user_id=actor_user_id
        )

    await session.flush()
    return applied, errors


def _compute_target(mode: str, value: int, current_stock: int) -> int:
    match mode:
        case "set":
            return value
        case "increment":
            return current_stock + value
        case "decrement":
            return current_stock - value
        case _:
            # Schema already constrains mode; defensive default.
            return current_stock


# silence unused-import warnings
_ = (Decimal,)
