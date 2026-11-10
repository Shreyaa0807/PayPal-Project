import secrets

from fastapi import Header, HTTPException

from .config import get_settings


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared key. If API_KEY is set, every /api route (except the PayPal webhook) requires it."""
    key = get_settings().api_key
    if key and not (x_api_key and secrets.compare_digest(x_api_key, key)):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def require_demo(x_api_key: str | None = Header(default=None)) -> None:
    s = get_settings()
    if not s.enable_demo:
        raise HTTPException(status_code=403, detail="Demo endpoints are disabled (ENABLE_DEMO=false).")
    if s.app_env == "production" and not s.api_key:
        raise HTTPException(status_code=403, detail="Demo endpoints in production require API_KEY to be configured.")
    require_api_key(x_api_key)
