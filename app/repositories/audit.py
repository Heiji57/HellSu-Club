from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def record(
        self,
        *,
        actor_discord_id: int | None,
        action: str,
        target_type: str,
        target_id: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        self._s.add(
            AuditLog(
                actor_discord_id=actor_discord_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                details=details or {},
            )
        )
