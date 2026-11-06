"""실제 PostgreSQL을 상대로 하는 통합 테스트 픽스처.

`docker compose up -d db` 후 실행한다. TEST_DATABASE_URL의 DB가 없으면 만들고,
세션 시작 시 마이그레이션을 base → head로 다시 적용해 스키마를 검증한다.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from types import SimpleNamespace

import asyncpg
import pytest
from alembic import command
from alembic.config import Config
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import text, update
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.container import Services
from app.core.clock import FixedClock
from app.core.database import Database
from app.core.week import KST
from app.models import (
    Attendance,
    AttendanceStatus,
    ReviewDecision,
    ReviewerType,
    Verification,
    VerificationReview,
    VerificationStatus,
)
from app.repositories.policy import PolicyRepository
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


# --- 시나리오 도우미: 고정 시각 2026-11-04(수) 12:00 KST, 이번 주는 11/2(월) ~ 11/8(일) ---

NOW = datetime(2026, 11, 4, 12, 0, tzinfo=KST)
LONG_AGO = datetime(2026, 10, 1, 12, 0, tzinfo=KST)


def kst(month: int, day: int, hour: int = 12) -> datetime:
    return datetime(2026, month, day, hour, tzinfo=KST)


@pytest.fixture
def services(db, clock, tmp_path) -> Services:
    settings = make_settings().model_copy(
        update={"image_dir": str(tmp_path), "initial_weekly_target": 3}
    )
    return Services.build(settings, db, clock, fetcher=FakeFetcher())


class Club:
    """테스트 시나리오를 만드는 도우미. 시각을 옮겨 가입·탈퇴·인증 시점을 정한다."""

    def __init__(self, services: Services, clock) -> None:
        self.s = services
        self.clock = clock
        self._ids = iter(range(70_000, 80_000))

    async def join(self, user_id: int, name: str, at: datetime = LONG_AGO) -> None:
        self.clock.set(at)
        await self.s.members.sync(user_id, name, has_member_role=True)
        self.clock.set(NOW)

    async def leave(self, user_id: int, at: datetime) -> None:
        self.clock.set(at)
        await self.s.members.deactivate(user_id, reason="서버 탈퇴")
        self.clock.set(NOW)

    async def verify(self, user_id: int, at: datetime, *, image: bytes | None = None, **kw):
        from app.services.verification_service import Submission

        attachment = self.s.fetcher.add(image) if image is not None else None
        return await self.s.verifications.submit(
            Submission(
                discord_user_id=user_id,
                interaction_id=next(self._ids),
                channel_id=1,
                submitted_at=at,
                content=kw.get("content", "스쿼트 5세트, 런지 3세트"),
                attachment=attachment,
            )
        )

    async def revoke(self, verification_id: int, reason: str) -> None:
        """관리자 출석 취소(Phase 4)를 흉내 낸다."""
        async with self.s.db.transaction() as s:
            await s.execute(
                update(Attendance)
                .where(Attendance.verification_id == verification_id)
                .values(status=AttendanceStatus.REVOKED, revoked_at=NOW)
            )
            await s.execute(
                update(Verification)
                .where(Verification.id == verification_id)
                .values(status=VerificationStatus.REJECTED)
            )
            s.add(
                VerificationReview(
                    verification_id=verification_id,
                    reviewer_type=ReviewerType.ADMIN,
                    actor_discord_id=1,
                    decision=ReviewDecision.REVOKE,
                    reason=reason,
                )
            )

    async def reject(self, verification_id: int, reason: str) -> None:
        async with self.s.db.transaction() as s:
            await s.execute(
                update(Verification)
                .where(Verification.id == verification_id)
                .values(status=VerificationStatus.REJECTED, decided_at=NOW)
            )
            s.add(
                VerificationReview(
                    verification_id=verification_id,
                    reviewer_type=ReviewerType.ADMIN,
                    actor_discord_id=1,
                    decision=ReviewDecision.REJECT,
                    reason=reason,
                )
            )

    async def add_policy(self, target: int, effective_from: date) -> None:
        async with self.s.db.transaction() as s:
            await PolicyRepository(s).add(
                weekly_target=target, effective_from=effective_from, created_by_discord_id=1
            )


@pytest.fixture
async def club(services, clock) -> Club:
    clock.set(NOW)
    return Club(services, clock)
