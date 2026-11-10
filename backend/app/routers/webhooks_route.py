from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from ..webhooks import process_webhook

router = APIRouter()  # NOT behind API_KEY: PayPal cannot send it. Trust comes from signature verification.


@router.post("/webhooks/paypal")
async def paypal_webhook(request: Request):
    raw = await request.body()
    headers = {k.lower(): v for k, v in request.headers.items()}
    status, body = await run_in_threadpool(process_webhook, raw, headers)
    return JSONResponse(body, status_code=status)
