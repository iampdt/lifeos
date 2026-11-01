from app.core.config import Settings
from app.db.session import async_url, libpq_url

NEON = (
    "postgresql://app:p%40ss@ep-cool-1.ap-southeast-1.aws.neon.tech/neondb"
    "?sslmode=require&channel_binding=require"
)


def test_neon_url_becomes_asyncpg_with_ssl():
    assert async_url(NEON) == (
        "postgresql+asyncpg://app:p%40ss@ep-cool-1.ap-southeast-1.aws.neon.tech/neondb?ssl=require"
    )


def test_libpq_form_for_the_checkpointer():
    url = libpq_url(NEON)
    assert url.startswith("postgresql://app:p%40ss@") and "sslmode=require" in url
    assert libpq_url("postgresql+asyncpg://u:p@h:5433/d?ssl=require") == (
        "postgresql://u:p@h:5433/d?sslmode=require"
    )


def test_local_urls_pass_through():
    local = "postgresql+asyncpg://lifeos:lifeos@localhost:5433/lifeos"
    assert async_url(local) == local
    assert Settings(_env_file=None, database_url=local).checkpoint_url == (
        "postgresql://lifeos:lifeos@localhost:5433/lifeos"
    )


def test_production_forces_secure_cookies():
    settings = Settings(_env_file=None, lifeos_env="production", lifeos_cookie_secure=False)
    assert settings.production and settings.lifeos_cookie_secure
