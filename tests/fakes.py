"""테스트 대역."""

import itertools

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
