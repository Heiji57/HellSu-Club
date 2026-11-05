"""/인증 명령 핸들러를 가짜 Interaction으로 실행해 봇 계층 연결을 확인한다.

Discord에 접속하지 않는다. 서비스와 DB는 실제로 동작한다.
"""

from datetime import datetime
from types import SimpleNamespace

import discord
import pytest
from sqlalchemy import select

from app.container import Services
from app.core.exceptions import NotAMember, WrongChannel
from app.core.week import KST
from app.models import Member, Verification
from bot.commands.verification import VerificationCommands
from bot.main import GymBot
from tests.fakes import FakeFetcher, make_settings
from tests.images import photo

ADMIN_CHANNEL = 100
USER_ID = 1001


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False

    async def defer(self, **kwargs) -> None:
        assert kwargs.get("ephemeral") is True
        self.deferred = True

    def is_done(self) -> bool:
        return self.deferred


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> None:
        self.sent.append(kwargs)


class FakeChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def _get_channel(self):  # Messageable 추상 메서드
        return self

    async def send(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(id=555_000 + len(self.sent))


_ids = iter(range(50_000, 60_000))


def interaction(bot: GymBot, *, day: int, channel_id: int = 777) -> SimpleNamespace:
    return SimpleNamespace(
        id=next(_ids),
        client=bot,
        user=SimpleNamespace(id=USER_ID, display_name="철수", mention=f"<@{USER_ID}>"),
        channel_id=channel_id,
        created_at=datetime(2026, 11, day, 12, 0, tzinfo=KST),
        response=FakeResponse(),
        followup=FakeFollowup(),
        command=None,
    )


def attachment(fetcher: FakeFetcher, data: bytes) -> SimpleNamespace:
    info = fetcher.add(data)
    return SimpleNamespace(url=info.url, filename=info.filename, size=info.size)


@pytest.fixture
async def env(db, clock, tmp_path, monkeypatch):
    settings = make_settings().model_copy(
        update={"admin_channel_id": ADMIN_CHANNEL, "image_dir": str(tmp_path)}
    )
    fetcher = FakeFetcher()
    services = Services.build(settings, db, clock, fetcher=fetcher)
    bot = GymBot(services)
    channel = FakeChannel()
    monkeypatch.setattr(bot, "get_channel", lambda cid: channel if cid == ADMIN_CHANNEL else None)
    monkeypatch.setattr("bot.commands.verification.is_member", lambda i: True)
    cog = VerificationCommands(bot)
    return SimpleNamespace(bot=bot, cog=cog, fetcher=fetcher, channel=channel, db=db)


async def run(env, inter, **kwargs) -> None:
    await VerificationCommands.verify.callback(env.cog, inter, **kwargs)


async def test_text_verification_registers_member_and_replies_privately(env):
    inter = interaction(env.bot, day=4)
    await run(env, inter, content="스쿼트 5세트, 런지 3세트")

    assert inter.response.deferred
    [reply] = inter.followup.sent
    assert reply["ephemeral"] is True
    assert reply["embed"].title == "✅ 인증 완료"
    assert env.channel.sent == []  # 승인은 관리자 채널에 올리지 않는다
    async with env.db.transaction() as s:
        member = await s.scalar(select(Member).where(Member.discord_user_id == USER_ID))
    assert member is not None and member.is_active  # 역할이 있으면 명령 실행 중에 등록된다


async def test_duplicate_photo_is_posted_to_admin_channel(env):
    await run(env, interaction(env.bot, day=3), photo=attachment(env.fetcher, photo(1)))
    inter = interaction(env.bot, day=4)
    await run(env, inter, photo=attachment(env.fetcher, photo(1)))

    assert inter.followup.sent[0]["embed"].title == "⏳ 관리자 확인 중"
    [post] = env.channel.sent
    assert post["embed"].title.startswith("🔍 검토 요청")
    assert isinstance(post["file"], discord.File)
    assert post["embed"].image.url == f"attachment://{post['file'].filename}"
    assert post["allowed_mentions"].users is False

    async with env.db.transaction() as s:
        v = await s.scalar(select(Verification).order_by(Verification.id.desc()))
    assert v.review_message_id == 555_001


async def test_wrong_channel(env):
    env.bot.services.settings.verification_channel_id = 888
    with pytest.raises(WrongChannel) as e:
        await run(env, interaction(env.bot, day=4, channel_id=777), content="스쿼트 5세트 했습니다")
    assert "<#888>" in e.value.user_message


async def test_non_member(env, monkeypatch):
    monkeypatch.setattr("bot.commands.verification.is_member", lambda i: False)
    inter = interaction(env.bot, day=4)
    with pytest.raises(NotAMember):
        await run(env, inter, content="스쿼트 5세트 했습니다")
    assert not inter.response.deferred
