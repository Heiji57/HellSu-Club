"""테스트 대역."""

import itertools
from datetime import datetime
from types import SimpleNamespace

import discord

from app.core.config import Settings
from app.services.verification_service import AttachmentInfo


def make_settings() -> Settings:
    return Settings(
        discord_token="test-token",
        guild_id=1,
        member_role_id=10,
        admin_role_id=20,
        admin_channel_id=100,
        report_channel_id=200,
        database_url="postgresql+asyncpg://u:p@localhost:1/none",
    )


class FakeFetcher:
    """URL → 바이트. 호출 기록을 남긴다."""

    def __init__(self) -> None:
        self.files: dict[str, bytes | Exception] = {}
        self.calls: list[str] = []
        self._ids = itertools.count(1)

    def add(self, data: bytes | Exception) -> AttachmentInfo:
        url = f"https://cdn.example/{next(self._ids)}.jpg"
        self.files[url] = data
        size = len(data) if isinstance(data, bytes) else 1000
        return AttachmentInfo(url=url, filename="photo.jpg", size=size)

    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        self.calls.append(url)
        data = self.files[url]
        if isinstance(data, Exception):
            raise data
        return data


# --- Discord 대역: 접속 없이 명령 핸들러를 실행한다 ---


class FakeMessage:
    def __init__(self, message_id: int) -> None:
        self.id = message_id
        self.edits: list[dict] = []

    async def edit(self, **kwargs) -> None:
        self.edits.append(kwargs)


class FakeResponse:
    def __init__(self) -> None:
        self.deferred = False
        self.sent: list[dict] = []
        self.edited: list[dict] = []

    async def defer(self, **kwargs) -> None:
        self.deferred = True
        self.defer_kwargs = kwargs

    def is_done(self) -> bool:
        return self.deferred or bool(self.sent) or bool(self.edited)

    async def send_message(self, content=None, **kwargs) -> None:
        self.sent.append({"content": content, **kwargs})

    async def edit_message(self, **kwargs) -> None:
        self.edited.append(kwargs)


class FakeFollowup:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send(self, **kwargs) -> FakeMessage:
        self.sent.append(kwargs)
        return FakeMessage(900_000 + len(self.sent))


class FakeChannel(discord.abc.Messageable):
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def _get_channel(self):  # Messageable 추상 메서드
        return self

    async def send(self, **kwargs) -> FakeMessage:
        self.sent.append(kwargs)
        return FakeMessage(555_000 + len(self.sent))


_interaction_ids = itertools.count(50_000)


def make_interaction(
    bot,
    *,
    user_id: int,
    display_name: str = "철수",
    created_at: datetime,
    channel_id: int = 777,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=next(_interaction_ids),
        client=bot,
        user=SimpleNamespace(id=user_id, display_name=display_name, mention=f"<@{user_id}>"),
        channel_id=channel_id,
        created_at=created_at,
        response=FakeResponse(),
        followup=FakeFollowup(),
        command=None,
    )
