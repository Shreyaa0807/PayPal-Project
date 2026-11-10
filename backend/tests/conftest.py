import os

# Force an isolated in-memory DB and blank secrets BEFORE the app is imported.
# (Real environment variables beat .env, so tests can never touch your Neon database.)
os.environ.update({
    "DATABASE_URL": "sqlite://", "PAYPAL_WEBHOOK_ID": "whid-test", "PAYPAL_CLIENT_ID": "", "PAYPAL_CLIENT_SECRET": "",
    "OPENROUTER_API_KEY": "", "API_KEY": "", "ENABLE_DEMO": "true", "APP_ENV": "test", "ALLOW_UNVERIFIED_WEBHOOKS": "false",
})

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import models  # noqa: E402,F401
from app.config import get_settings  # noqa: E402
from app.db import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402

assert engine.url.drivername.startswith("sqlite"), "Tests must never run against a real database"


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture
def db():
    with SessionLocal() as session:
        yield session


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture
def settings():
    return get_settings()
