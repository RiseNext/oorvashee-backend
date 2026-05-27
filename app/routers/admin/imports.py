"""Admin CSV bulk-import endpoints.

Three upload endpoints (product / variant / inventory) + tracking +
templates. Uploads are dispatched to `BackgroundTasks` so the admin gets
the job_id back immediately and polls status separately.

Body-size middleware exempts this prefix (configured via
`BODY_SIZE_EXEMPT_PATH_PREFIXES`) — CSVs legitimately exceed the 1 MB
JSON cap. The service enforces a hard 50 MB ceiling regardless.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)

from app.core.pagination import OffsetParams, Page, offset_params
from app.core.security import get_current_user, require_role
from app.db.deps import DbSession
from app.models.enums import ImportKind, ImportStatus
from app.models.user import User
from app.schemas.admin_import import (
    ImportJobListItem,
    ImportJobRead,
    TemplateKind,
)
from app.services.admin_import_service import AdminImportService, run_import_job

router = APIRouter(dependencies=[Depends(require_role("admin"))])

CurrentAdmin = Annotated[User, Depends(get_current_user)]


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


# =============================================================================
# Job tracking
# =============================================================================


@router.get(
    "",
    response_model=Page[ImportJobListItem],
    summary="List import jobs (newest first)",
)
async def list_jobs(
    session: DbSession,
    params: Annotated[OffsetParams, Depends(offset_params)],
    kind: Annotated[ImportKind | None, Query()] = None,
    status_: Annotated[ImportStatus | None, Query(alias="status")] = None,
) -> Page[ImportJobListItem]:
    return await AdminImportService(session).list_jobs(
        params=params, kind=kind, status=status_,
    )


@router.get(
    "/{job_id}",
    response_model=ImportJobRead,
    summary="Get import job detail — status, progress, row errors",
)
async def get_job(
    session: DbSession,
    job_id: Annotated[uuid.UUID, Path()],
) -> ImportJobRead:
    return await AdminImportService(session).get_job(job_id)


# =============================================================================
# Uploads — one endpoint per kind
# =============================================================================


async def _upload_helper(
    *,
    kind: ImportKind,
    file: UploadFile,
    background_tasks: BackgroundTasks,
    session,  # type: ignore[no-untyped-def]
    admin: User,
    dry_run: bool,
    request: Request,
) -> ImportJobRead:
    """Common path for all three upload endpoints."""
    # `read()` slurps the file into memory. UploadFile uses a spooled
    # temp file under the hood, so this is safe for tens-of-MB CSVs.
    # The service enforces a 50 MB hard cap.
    csv_bytes = await file.read()
    job = await AdminImportService(session).create_job(
        kind=kind,
        csv_bytes=csv_bytes,
        filename=file.filename,
        dry_run=dry_run,
        actor_user_id=admin.id,
        request_id=_request_id(request),
    )
    await session.commit()  # persist the queued job BEFORE scheduling the task

    # `run_import_job` opens its own session via `session_scope()` —
    # the request-scoped session that ran `create_job` is closing now
    # but the background task is independent.
    background_tasks.add_task(
        run_import_job,
        job.id,
        csv_bytes,
        kind,
        dry_run=dry_run,
        actor_user_id=admin.id,
    )
    return ImportJobRead.model_validate(job)


@router.post(
    "/products",
    response_model=ImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk-import products from CSV. Returns job_id immediately.",
)
async def upload_products(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
    admin: CurrentAdmin,
    file: Annotated[UploadFile, File(description="CSV with product columns")],
    dry_run: Annotated[bool, Query()] = False,
) -> ImportJobRead:
    return await _upload_helper(
        kind=ImportKind.PRODUCT,
        file=file,
        background_tasks=background_tasks,
        session=session,
        admin=admin,
        dry_run=dry_run,
        request=request,
    )


@router.post(
    "/variants",
    response_model=ImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk-import variants + initial inventory rows from CSV.",
)
async def upload_variants(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
    admin: CurrentAdmin,
    file: Annotated[UploadFile, File(description="CSV with variant columns")],
    dry_run: Annotated[bool, Query()] = False,
) -> ImportJobRead:
    return await _upload_helper(
        kind=ImportKind.VARIANT,
        file=file,
        background_tasks=background_tasks,
        session=session,
        admin=admin,
        dry_run=dry_run,
        request=request,
    )


@router.post(
    "/inventory",
    response_model=ImportJobRead,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Bulk-update inventory (set/increment/decrement) from CSV.",
)
async def upload_inventory(
    request: Request,
    background_tasks: BackgroundTasks,
    session: DbSession,
    admin: CurrentAdmin,
    file: Annotated[UploadFile, File(description="CSV with inventory columns")],
    dry_run: Annotated[bool, Query()] = False,
) -> ImportJobRead:
    return await _upload_helper(
        kind=ImportKind.INVENTORY,
        file=file,
        background_tasks=background_tasks,
        session=session,
        admin=admin,
        dry_run=dry_run,
        request=request,
    )


# =============================================================================
# Templates — admin downloads a starter CSV
# =============================================================================


_TEMPLATES = {
    TemplateKind.PRODUCT: (
        "slug,name,short_description,description,base_price,mrp,currency,"
        "tags,featured,is_bestseller,is_new,status,seo_title,seo_description\n"
        "kanchipuram-sample,Kanchipuram Sample Silk,Brief tagline,"
        "Long description here,7499,9999,INR,silk|bridal|kanchipuram,"
        "false,false,true,draft,SEO title example,SEO description example\n"
    ),
    TemplateKind.VARIANT: (
        "product_slug,sku,color,fabric,size,price_override,weight_grams,"
        "is_default,is_active,initial_stock,low_stock_threshold\n"
        "kanchipuram-sample,KCH-001-MAROON,Maroon,Kanchipuram Silk,Free,"
        ",550,true,true,5,2\n"
    ),
    TemplateKind.INVENTORY: (
        "sku,mode,value,reason,note\n"
        "KCH-001-MAROON,set,10,restock,Replenished from supplier\n"
        "KCH-001-MAROON,increment,5,restock,Additional units arrived\n"
    ),
}


@router.get(
    "/templates/{kind}",
    summary="Download an empty CSV template for the given import kind.",
)
async def download_template(
    kind: Annotated[TemplateKind, Path()],
) -> Response:
    body = _TEMPLATES[kind]
    return Response(
        content=body,
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="oorvashee-{kind.value}-template.csv"',
        },
    )
