"""실제 PostgreSQL을 상대로 하는 통합 테스트 픽스처.

`docker compose up -d db` 후 실행한다. TEST_DATABASE_URL의 DB가 없으면 만들고,
세션 시작 시 마이그레이션을 base → head로 다시 적용해 스키마를 검증한다.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.container import Services
from app.core.clock import FixedClock
from app.core.database import Database
from tests.fakes import FakeChannel, FakeFetcher, make_settings


class _TestSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", env_ignore_empty=True)
    test_database_url: str = "postgresql+asyncpg://gym:gym@localhost:5432/gym_test"


TEST_DATABASE_URL = _TestSettings().test_database_url


def pytest_collection_modifyitems(items):
    for item in items:
        if "tests/integration" in str(item.path):
            item.add_marker(pytest.mark.integration)


async def _ensure_database(url: str) -> None:
    u = make_url(url)
    conn = await asyncpg.connect(
        user=u.username, password=u.password, host=u.host, port=u.port, database="postgres"
    )
    try:
        exists = await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", u.database)
        if not exists:
            await conn.execute(f'CREATE DATABASE "{u.database}"')
    finally:
        await conn.close()


@pytest.fixture(scope="session")
def migrated_url() -> str:
    try:
        asyncio.run(_ensure_database(TEST_DATABASE_URL))
    except (OSError, asyncpg.PostgresError) as e:
        pytest.fail(
            f"통합 테스트 DB에 연결할 수 없습니다 ({e}). "
            "`docker compose up -d db`를 먼저 실행하세요.",
            pytrace=False,
        )
    cfg = Config("alembic.ini")
    cfg.attributes["database_url"] = TEST_DATABASE_URL
    cfg.attributes["configure_logging"] = False
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    return TEST_DATABASE_URL


@pytest.fixture
async def db(migrated_url: str) -> AsyncIterator[Database]:
    database = Database(migrated_url, poolclass=NullPool)
    async with database.transaction() as s:
        tables = (
            (
                await s.execute(
                    text(
                        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
                        " AND tablename <> 'alembic_version'"
                    )
                )
            )
            .scalars()
            .all()
        )
        if tables:
            joined = ", ".join(f'"{t}"' for t in tables)
            await s.execute(text(f"TRUNCATE {joined} RESTART IDENTITY CASCADE"))
    yield database
    await database.dispose()


@pytest.fixture
def clock() -> FixedClock:
    # 2026-11-04(수) 12:00 KST
    return FixedClock(datetime(2026, 11, 4, 3, 0, tzinfo=UTC))


ADMIN_CHANNEL_ID = 100


@pytest.fixture
def bot_env(db, clock, tmp_path, monkeypatch):
    """명령 핸들러 테스트용 봇. 역할은 env.member / env.admin으로 바꾼다."""
    from bot.main import GymBot  # discord.py import 비용을 이 픽스처를 쓰는 테스트로 한정

    settings = make_settings().model_copy(
        update={"admin_channel_id": ADMIN_CHANNEL_ID, "image_dir": str(tmp_path / "images")}
    )
    fetcher = FakeFetcher()
    bot = GymBot(Services.build(settings, db, clock, fetcher=fetcher))
    channel = FakeChannel()
    env = SimpleNamespace(
        bot=bot, db=db, clock=clock, fetcher=fetcher, channel=channel, member=True, admin=False
    )
    monkeypatch.setattr(
        bot, "get_channel", lambda cid: channel if cid == ADMIN_CHANNEL_ID else None
    )
    monkeypatch.setattr("bot.checks.is_member", lambda i: env.member)
    monkeypatch.setattr("bot.checks.is_admin", lambda i: env.admin)
    monkeypatch.setattr("bot.commands.me.is_admin", lambda i: env.admin)
    return env
