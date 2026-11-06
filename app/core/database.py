import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


class Database:
    """비동기 엔진과 세션 팩토리를 소유한다. 트랜잭션 경계는 서비스가 `transaction()`으로 정한다."""

    def __init__(self, url: str, *, echo: bool = False, **engine_kwargs) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, echo=echo, pool_pre_ping=True, **engine_kwargs
        )
        self._sessions = async_sessionmaker(self.engine, expire_on_commit=False)

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """하나의 트랜잭션. 블록이 정상 종료되면 커밋, 예외가 나면 롤백한다."""
        async with self._sessions() as session, session.begin():
            yield session

    async def ping(self) -> float:
        """DB 왕복 시간(ms). 연결 실패 시 예외를 그대로 올린다."""
        started = time.perf_counter()
        async with self.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return (time.perf_counter() - started) * 1000

    async def dispose(self) -> None:
        await self.engine.dispose()
