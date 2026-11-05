"""첨부 이미지 다운로드. Discord 첨부 URL은 만료되므로 제출 즉시 받는다 (REQUIREMENTS 9)."""

import logging
from typing import Protocol

import aiohttp

from app.core.exceptions import ImageTooLarge, ImageUnreadable

log = logging.getLogger(__name__)

CHUNK_SIZE = 64 * 1024


class ImageFetcher(Protocol):
    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        """이미지를 받는다. 너무 크면 ImageTooLarge, 받지 못하면 ImageUnreadable."""
        ...


class HttpImageFetcher:
    """용량 제한을 넘으면 끝까지 받지 않고 중단한다."""

    def __init__(self, *, timeout_seconds: float = 30, allow_http: bool = False) -> None:
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._allow_http = allow_http
        self._session: aiohttp.ClientSession | None = None

    async def fetch(self, url: str, *, max_bytes: int) -> bytes:
        if not (url.startswith("https://") or (self._allow_http and url.startswith("http://"))):
            raise ImageUnreadable()
        try:
            async with self._get_session().get(url) as resp:
                if resp.status != 200:
                    log.warning("이미지 다운로드 실패: HTTP %s", resp.status)
                    raise ImageUnreadable()
                if resp.content_length is not None and resp.content_length > max_bytes:
                    raise ImageTooLarge.for_limit(max_bytes)
                buf = bytearray()
                async for chunk in resp.content.iter_chunked(CHUNK_SIZE):
                    buf.extend(chunk)
                    if len(buf) > max_bytes:
                        raise ImageTooLarge.for_limit(max_bytes)
                return bytes(buf)
        except (aiohttp.ClientError, TimeoutError) as e:
            log.warning("이미지 다운로드 실패: %r", e)
            raise ImageUnreadable() from e

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
