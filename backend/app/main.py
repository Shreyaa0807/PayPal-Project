from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .routers import dashboard, demo, incidents, paypal_routes, webhooks_route


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()  # creates tables if missing (Neon Postgres or SQLite)
    yield


settings = get_settings()
app = FastAPI(title="PayPal PaymentOps AI", version="1.0.0", lifespan=lifespan,
              description="Evidence-grounded payment failure investigator. AI explains; deterministic rules decide what is safe.")

app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=False,
                   allow_methods=["*"], allow_headers=["*"])

for r in (dashboard.router, incidents.router, paypal_routes.router, webhooks_route.router, demo.router):
    app.include_router(r, prefix="/api")


@app.get("/api/health")
def health():
    s = get_settings()
    return {
        "status": "ok", "env": s.app_env, "paypal_configured": s.paypal_configured, "paypal_env": s.paypal_env,
        "webhook_verification_configured": bool(s.paypal_webhook_id), "llm_configured": bool(s.openrouter_api_key),
        "llm_models": s.llm_models if s.openrouter_api_key else [], "demo_enabled": s.enable_demo,
    }


@app.exception_handler(Exception)
async def unhandled(_: Request, exc: Exception):
    # Fail safely: never leak internals, secrets or stack traces to clients.
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
