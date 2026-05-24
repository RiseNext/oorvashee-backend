"""Public payment endpoints — frontend-initiated verify."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.idempotency import IdempotencyDep, with_idempotency
from app.core.rate_limit import profiles
from app.db.deps import DbSession
from app.schemas.checkout import OrderRead, VerifyPaymentRequest
from app.services.payment_service import PaymentService

router = APIRouter()


@router.post(
    "/verify",
    response_model=OrderRead,
    summary="Verify Razorpay payment signature + finalise the order (idempotent)",
)
@profiles.checkout()
async def verify_payment(
    request: Request,
    body: VerifyPaymentRequest,
    session: DbSession,
    response: Response,
    ctx: IdempotencyDep,
) -> dict:
    async def execute() -> dict:
        order = await PaymentService(session).verify_and_capture(
            order_number=body.order_number,
            razorpay_order_id=body.razorpay_order_id,
            razorpay_payment_id=body.razorpay_payment_id,
            razorpay_signature=body.razorpay_signature,
            request_id=getattr(request.state, "request_id", None),
        )
        return PaymentService.order_to_read(order).model_dump(mode="json")

    status_code, payload = await with_idempotency(
        ctx=ctx,
        session=session,
        user_id=None,
        status_code=status.HTTP_200_OK,
        execute=execute,
    )
    response.status_code = status_code
    return payload
