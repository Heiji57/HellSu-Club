from dataclasses import dataclass

from app.core.clock import Clock
from app.core.config import Settings
from app.core.database import Database
from app.image.fetcher import HttpImageFetcher, ImageFetcher
from app.image.storage import ImageStorage
from app.services.attendance_service import AttendanceService
from app.services.member_service import MemberService
from app.services.notification_service import NotificationService
from app.services.policy_service import PolicyService
from app.services.review_service import ReviewService
from app.services.statistics_service import StatisticsService
from app.services.verification_service import VerificationService


@dataclass(frozen=True)
class Services:
    """조립 루트. 봇은 이 객체를 통해서만 서비스에 접근한다."""

    settings: Settings
    db: Database
    clock: Clock
    fetcher: ImageFetcher
    storage: ImageStorage
    members: MemberService
    policies: PolicyService
    attendance: AttendanceService
    verifications: VerificationService
    statistics: StatisticsService
    reviews: ReviewService
    notifications: NotificationService

    @classmethod
    def build(
        cls,
        settings: Settings,
        db: Database,
        clock: Clock,
        *,
        fetcher: ImageFetcher | None = None,
    ) -> "Services":
        fetcher = fetcher or HttpImageFetcher()
        storage = ImageStorage(settings.image_dir)
        attendance = AttendanceService(db)
        return cls(
            settings=settings,
            db=db,
            clock=clock,
            fetcher=fetcher,
            storage=storage,
            members=MemberService(db, clock),
            policies=PolicyService(db, clock, initial_weekly_target=settings.initial_weekly_target),
            attendance=attendance,
            verifications=VerificationService(
                db,
                clock,
                attendance=attendance,
                fetcher=fetcher,
                storage=storage,
                text_min_length=settings.text_min_length,
                image_max_bytes=settings.image_max_bytes,
                phash_threshold=settings.phash_distance_threshold,
                default_weekly_target=settings.initial_weekly_target,
            ),
            statistics=StatisticsService(
                db, clock, default_weekly_target=settings.initial_weekly_target
            ),
            reviews=ReviewService(db, clock, attendance=attendance, storage=storage),
            notifications=NotificationService(db, clock),
        )

    async def aclose(self) -> None:
        close = getattr(self.fetcher, "aclose", None)
        if close is not None:
            await close()
