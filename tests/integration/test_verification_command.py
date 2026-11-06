"""/인증 명령 핸들러를 가짜 Interaction으로 실행해 봇 계층 연결을 확인한다.

Discord에 접속하지 않는다. 서비스와 DB는 실제로 동작한다.
"""

from datetime import datetime
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import func, select

from app.core.exceptions import NotAMember, WrongChannel
from app.core.week import KST
from app.models import Member, Verification
from bot.commands.verification import VerificationCommands
from tests.fakes import make_interaction
from tests.images import photo

USER_ID = 1001


def interaction(env, *, day: int, channel_id: int = 777):
    return make_interaction(
        env.bot,
        user_id=USER_ID,
        created_at=datetime(2026, 11, day, 12, 0, tzinfo=KST),
        channel_id=channel_id,
    )


def attachment(env, data: bytes) -> SimpleNamespace:
    info = env.fetcher.add(data)
    return SimpleNamespace(url=info.url, filename=info.filename, size=info.size)


async def run(env, inter, **kwargs) -> None:
    await VerificationCommands.verify.callback(VerificationCommands(env.bot), inter, **kwargs)


async def test_text_verification_registers_member_and_replies_privately(bot_env):
    inter = interaction(bot_env, day=4)
    await run(bot_env, inter, content="스쿼트 5세트, 런지 3세트")

    assert inter.response.defer_kwargs["ephemeral"] is True
    [reply] = inter.followup.sent
    assert reply["ephemeral"] is True
    assert reply["embed"].title == "✅ 인증 완료"
    assert bot_env.channel.sent == []  # 승인은 관리자 채널에 올리지 않는다
    async with bot_env.db.transaction() as s:
        member = await s.scalar(select(Member).where(Member.discord_user_id == USER_ID))
    assert member is not None and member.is_active  # 역할이 있으면 명령 실행 중에 등록된다


async def test_duplicate_photo_is_posted_to_admin_channel(bot_env):
    await run(bot_env, interaction(bot_env, day=3), photo=attachment(bot_env, photo(1)))
    inter = interaction(bot_env, day=4)
    await run(bot_env, inter, photo=attachment(bot_env, photo(1)))

    assert inter.followup.sent[0]["embed"].title == "⏳ 관리자 확인 중"
    [post] = bot_env.channel.sent
    assert post["embed"].title.startswith("🔍 검토 요청")
    assert isinstance(post["file"], discord.File)
    assert post["embed"].image.url == f"attachment://{post['file'].filename}"
    assert post["allowed_mentions"].users is False

    async with bot_env.db.transaction() as s:
        v = await s.scalar(select(Verification).order_by(Verification.id.desc()))
    assert v.review_message_id == 555_001


async def test_wrong_channel(bot_env):
    bot_env.bot.services.settings.verification_channel_id = 888
    inter = interaction(bot_env, day=4, channel_id=777)
    with pytest.raises(WrongChannel) as e:
        await run(bot_env, inter, content="스쿼트 5세트 했습니다")
    assert "<#888>" in e.value.user_message
    assert not inter.response.deferred


async def test_non_member(bot_env):
    bot_env.member = False
    with pytest.raises(NotAMember):
        await run(bot_env, interaction(bot_env, day=4), content="스쿼트 5세트 했습니다")
    async with bot_env.db.transaction() as s:
        assert await s.scalar(select(func.count()).select_from(Verification)) == 0
