from dataclasses import dataclass

from app.core.clock import Clock
from app.core.config import Settings
from app.core.database import Database
from app.services.member_service import MemberService


@dataclass(frozen=True)
class Services:
    """조립 루트. 봇은 이 객체를 통해서만 서비스에 접근한다."""

    settings: Settings
    db: Database
    clock: Clock
    members: MemberService

    @classmethod
    def build(cls, settings: Settings, db: Database, clock: Clock) -> "Services":
        return cls(
            settings=settings,
            db=db,
            clock=clock,
            members=MemberService(db, clock),
        )
