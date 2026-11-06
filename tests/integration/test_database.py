import pytest
from sqlalchemy import func, select

from app.models import Member, MemberStatus


async def test_ping_returns_latency(db):
    assert await db.ping() >= 0


async def test_transaction_rolls_back_on_error(db, clock):
    with pytest.raises(RuntimeError):
        async with db.transaction() as s:
            s.add(
                Member(
                    discord_user_id=1,
                    display_name="롤백",
                    status=MemberStatus.ACTIVE,
                    joined_at=clock.now(),
                )
            )
            await s.flush()
            raise RuntimeError("중간 실패")

    async with db.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(Member)) == 0
