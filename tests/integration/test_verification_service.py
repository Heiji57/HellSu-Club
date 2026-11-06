"""/인증 제출 흐름 전체를 실제 PostgreSQL로 검증한다. 이미지 다운로드만 가짜로 바꾼다."""

import asyncio
import io
import itertools
from datetime import date, datetime
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    AlreadyVerifiedToday,
    EmptySubmission,
    ImageTooLarge,
    ImageUnreadable,
    InactiveMember,
    NotAMember,
    TextTooShort,
    UnsupportedImageFormat,
)
from app.core.week import KST
from app.image.storage import ImageStorage
from app.models import (
    Attendance,
    AttendanceStatus,
    ClubPolicy,
    Verification,
    VerificationReview,
    VerificationStatus,
)
from app.services.attendance_service import AttendanceService
from app.services.member_service import MemberService
from app.services.policy_service import PolicyService
from app.services.verification_service import AttachmentInfo, Submission, VerificationService
from tests.fakes import FakeFetcher
from tests.images import encode, exif_with_location, photo, scene

CHULSOO, YOUNGHEE, OUTSIDER = 1001, 1002, 9999
MAX_BYTES = 2_000_000


class BrokenStorage(ImageStorage):
    async def save(self, relative: str, data: bytes) -> Path:
        raise OSError("디스크 가득 참")


def kst(day: int, hour: int = 12, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 11, day, hour, minute, second, tzinfo=KST)


_interaction_ids = itertools.count(10_000)


def submission(
    user: int = CHULSOO,
    *,
    at: datetime | None = None,
    content: str | None = "스쿼트 5세트, 벤치프레스 5세트",
    attachment: AttachmentInfo | None = None,
    interaction_id: int | None = None,
) -> Submission:
    return Submission(
        discord_user_id=user,
        interaction_id=interaction_id or next(_interaction_ids),
        channel_id=555,
        submitted_at=at or kst(4),
        content=content,
        attachment=attachment,
    )


@pytest.fixture
def fetcher() -> FakeFetcher:
    return FakeFetcher()


@pytest.fixture
def storage(tmp_path) -> ImageStorage:
    return ImageStorage(tmp_path / "images")


def make_service(db, clock, fetcher, storage, **overrides) -> VerificationService:
    options = {
        "text_min_length": 10,
        "image_max_bytes": MAX_BYTES,
        "phash_threshold": 4,
        "default_weekly_target": 3,
    } | overrides
    return VerificationService(
        db, clock, attendance=AttendanceService(db), fetcher=fetcher, storage=storage, **options
    )


@pytest.fixture
async def service(db, clock, fetcher, storage) -> VerificationService:
    members = MemberService(db, clock)
    await members.sync(CHULSOO, "철수", has_member_role=True)
    await members.sync(YOUNGHEE, "영희", has_member_role=True)
    return make_service(db, clock, fetcher, storage)


async def count(db, model) -> int:
    async with db.transaction() as s:
        return await s.scalar(select(func.count()).select_from(model))


async def get_verification(db, verification_id: int) -> Verification:
    async with db.transaction() as s:
        return await s.get(Verification, verification_id)


class TestSubmissionForms:
    async def test_text_only_is_approved_with_attendance(self, db, service):
        result = await service.submit(submission(content="  런닝머신 30분, 스쿼트 5세트  "))

        assert result.status is VerificationStatus.APPROVED
        assert (result.workout_date, result.week_count, result.week_target) == (
            date(2026, 11, 4),
            1,
            3,
        )
        v = await get_verification(db, result.verification_id)
        assert v.content == "런닝머신 30분, 스쿼트 5세트"
        assert v.image_sha256 is None and v.image_path is None
        assert v.decided_at is not None
        assert v.result_notified_at is not None  # 바로 응답했으므로 지난 결과 알림 대상이 아니다
        async with db.transaction() as s:
            attendance = await s.scalar(select(Attendance))
            review = await s.scalar(select(VerificationReview))
        assert attendance.verification_id == v.id
        assert attendance.workout_date == date(2026, 11, 4)
        assert attendance.status == AttendanceStatus.ACTIVE
        assert (review.reviewer_type, review.decision, review.actor_discord_id) == (
            "RULE",
            "APPROVE",
            None,
        )

    async def test_image_only_is_approved_and_stored_without_metadata(
        self, db, service, fetcher, storage
    ):
        raw = photo(exif=exif_with_location())
        result = await service.submit(submission(content=None, attachment=fetcher.add(raw)))

        assert result.status is VerificationStatus.APPROVED
        v = await get_verification(db, result.verification_id)
        assert v.content is None
        assert v.image_mime == "image/jpeg"
        assert v.image_bytes == len(raw)
        assert len(v.image_sha256) == 64 and len(v.image_phash) == 16
        assert v.image_path == f"2026/11/{v.id}.jpg"
        assert result.image_file == storage.absolute(v.image_path)
        stored = Image.open(io.BytesIO(result.image_file.read_bytes()))
        assert not stored.getexif()  # 위치 정보가 디스크에 남지 않는다

    async def test_short_text_with_image_is_approved(self, service, fetcher):
        result = await service.submit(submission(content="오운완", attachment=fetcher.add(photo())))
        assert result.status is VerificationStatus.APPROVED
        assert result.content == "오운완"


class TestInputRejection:
    """입력 오류는 저장하지 않고 안내만 한다 (REQUIREMENTS 3.3)."""

    @pytest.mark.parametrize(
        ("content", "data", "error"),
        [
            (None, None, EmptySubmission),
            ("    ", None, EmptySubmission),
            ("운동함", None, TextTooShort),
            (None, encode(scene(1), "GIF"), UnsupportedImageFormat),
            (None, b"not an image at all", UnsupportedImageFormat),
            (None, photo()[:3000], ImageUnreadable),
            (None, ImageUnreadable(), ImageUnreadable),
            (None, ImageTooLarge.for_limit(MAX_BYTES), ImageTooLarge),
        ],
        ids=[
            "empty",
            "whitespace",
            "short-text",
            "gif",
            "not-image",
            "corrupt",
            "download-failed",
            "too-large-while-downloading",
        ],
    )
    async def test_rejected_without_saving(self, db, service, fetcher, content, data, error):
        attachment = fetcher.add(data) if data is not None else None
        with pytest.raises(error):
            await service.submit(submission(content=content, attachment=attachment))
        assert await count(db, Verification) == 0
        assert await count(db, Attendance) == 0

    async def test_too_large_by_metadata_is_not_downloaded(self, db, service, fetcher):
        info = fetcher.add(photo())
        big = AttachmentInfo(url=info.url, filename=info.filename, size=MAX_BYTES + 1)
        with pytest.raises(ImageTooLarge) as e:
            await service.submit(submission(attachment=big))
        assert "MB 이하" in e.value.user_message
        assert fetcher.calls == []
        assert await count(db, Verification) == 0


class TestOncePerDay:
    async def test_second_submission_same_day_is_blocked_before_download(
        self, db, service, fetcher
    ):
        await service.submit(submission(at=kst(4, 7)))
        with pytest.raises(AlreadyVerifiedToday):
            await service.submit(submission(at=kst(4, 21), attachment=fetcher.add(photo())))
        assert fetcher.calls == []
        assert await count(db, Verification) == 1

    async def test_next_day_is_allowed(self, service):
        await service.submit(submission(at=kst(4, 23, 59, 59)))
        result = await service.submit(submission(at=kst(5, 0, 0, 0)))
        assert result.workout_date == date(2026, 11, 5)
        assert result.week_count == 2

    async def test_pending_review_also_blocks(self, service, fetcher):
        await service.submit(submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1))))
        pending = await service.submit(submission(attachment=fetcher.add(photo(1))))
        assert pending.status is VerificationStatus.REVIEW_REQUIRED
        with pytest.raises(AlreadyVerifiedToday):
            await service.submit(submission())

    async def test_resubmission_allowed_after_rejection(self, db, clock, service, fetcher):
        await service.submit(submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1))))
        pending = await service.submit(submission(attachment=fetcher.add(photo(1))))
        async with db.transaction() as s:  # 관리자 거절(Phase 4)을 흉내 낸다
            await s.execute(
                update(Verification)
                .where(Verification.id == pending.verification_id)
                .values(status=VerificationStatus.REJECTED, decided_at=clock.now())
            )
        result = await service.submit(submission(attachment=fetcher.add(photo(2))))
        assert result.status is VerificationStatus.APPROVED

    async def test_concurrent_submissions_create_one(self, db, service):
        results = await asyncio.gather(
            *(service.submit(submission()) for _ in range(5)), return_exceptions=True
        )
        successes = [r for r in results if not isinstance(r, BaseException)]
        failures = [r for r in results if isinstance(r, BaseException)]
        assert len(successes) == 1
        assert all(isinstance(f, AlreadyVerifiedToday) for f in failures), failures
        assert await count(db, Verification) == 1
        assert await count(db, Attendance) == 1

    async def test_same_interaction_processed_once(self, db, service):
        await service.submit(submission(interaction_id=777, at=kst(4)))
        with pytest.raises(AlreadyVerifiedToday):
            await service.submit(submission(interaction_id=777, at=kst(5)))
        assert await count(db, Verification) == 1


class TestDuplicateImages:
    async def test_identical_file_from_another_member_needs_review(self, db, service, fetcher):
        first = await service.submit(
            submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1)))
        )
        result = await service.submit(submission(content=None, attachment=fetcher.add(photo(1))))

        assert result.status is VerificationStatus.REVIEW_REQUIRED
        assert result.week_count == 0
        [match] = result.matches
        assert (match.verification_id, match.exact, match.distance) == (
            first.verification_id,
            True,
            0,
        )
        assert match.ref is not None and match.ref.member_display_name == "영희"
        assert match.ref.workout_date == date(2026, 11, 3)

        v = await get_verification(db, result.verification_id)
        assert v.decided_at is None and v.result_notified_at is None
        assert v.image_path is not None  # 관리자가 확인할 수 있게 파일은 저장한다
        async with db.transaction() as s:
            review = await s.scalar(
                select(VerificationReview).where(
                    VerificationReview.verification_id == result.verification_id
                )
            )
            assert await s.scalar(select(func.count()).select_from(Attendance)) == 1
        assert review.decision == "REVIEW"
        assert review.details["matches"][0] == {
            "verification_id": first.verification_id,
            "distance": 0,
            "exact": True,
        }

    async def test_resized_recompressed_copy_needs_review(self, service, fetcher):
        await service.submit(submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1))))
        copy = encode(scene(1).resize((400, 300)), "JPEG", quality=60)
        result = await service.submit(submission(attachment=fetcher.add(copy)))

        assert result.status is VerificationStatus.REVIEW_REQUIRED
        [match] = result.matches
        assert not match.exact and match.distance <= 4

    async def test_reusing_own_old_photo_needs_review(self, service, fetcher):
        await service.submit(submission(at=kst(2), attachment=fetcher.add(photo(1))))
        result = await service.submit(submission(at=kst(4), attachment=fetcher.add(photo(1))))
        assert result.status is VerificationStatus.REVIEW_REQUIRED

    async def test_different_photo_is_approved(self, service, fetcher):
        await service.submit(submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1))))
        result = await service.submit(submission(attachment=fetcher.add(photo(2))))
        assert result.status is VerificationStatus.APPROVED
        assert result.matches == ()

    async def test_detection_survives_file_cleanup(self, db, service, fetcher):
        # 파일을 지워도 해시는 남아 있으므로 계속 탐지한다 (REQUIREMENTS 9)
        first = await service.submit(
            submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1)))
        )
        async with db.transaction() as s:
            await s.execute(
                update(Verification)
                .where(Verification.id == first.verification_id)
                .values(image_path=None)
            )
        result = await service.submit(submission(attachment=fetcher.add(photo(1))))
        assert result.status is VerificationStatus.REVIEW_REQUIRED


class TestMembership:
    async def test_inactive_member(self, db, clock, service):
        await MemberService(db, clock).deactivate(CHULSOO, reason="테스트")
        with pytest.raises(InactiveMember):
            await service.submit(submission())

    async def test_unregistered_user(self, service):
        with pytest.raises(NotAMember):
            await service.submit(submission(OUTSIDER))


class TestWeeklyProgress:
    async def test_week_boundary_uses_submission_time(self, service):
        # 2026-11-08은 일요일, 11-09는 월요일
        sunday = await service.submit(submission(at=kst(8, 23, 59, 59)))
        monday = await service.submit(submission(at=kst(9, 0, 0, 0)))
        assert (sunday.workout_date, sunday.week_count) == (date(2026, 11, 8), 1)
        assert (monday.workout_date, monday.week_count) == (date(2026, 11, 9), 1)

    async def test_just_achieved_on_reaching_target(self, service):
        results = [await service.submit(submission(at=kst(day))) for day in (2, 3, 4, 5)]
        assert [r.week_count for r in results] == [1, 2, 3, 4]
        assert [r.just_achieved for r in results] == [False, False, True, False]

    async def test_target_comes_from_policy(self, db, clock, service):
        policies = PolicyService(db, clock, initial_weekly_target=2)
        assert await policies.ensure_initial() is True
        assert await policies.ensure_initial() is False  # 두 번 시드하지 않는다
        assert await count(db, ClubPolicy) == 1

        result = await service.submit(submission())
        assert result.week_target == 2


class TestImageStorageFailure:
    async def test_verification_kept_when_file_save_fails(self, db, clock, fetcher, tmp_path):
        await MemberService(db, clock).sync(CHULSOO, "철수", has_member_role=True)
        service = make_service(db, clock, fetcher, BrokenStorage(tmp_path))
        result = await service.submit(submission(attachment=fetcher.add(photo())))

        assert result.status is VerificationStatus.APPROVED
        assert result.image_file is None
        v = await get_verification(db, result.verification_id)
        assert v.image_path is None and v.image_sha256 is not None


class TestReviewMessage:
    async def test_set_review_message(self, db, service, fetcher):
        await service.submit(submission(YOUNGHEE, at=kst(3), attachment=fetcher.add(photo(1))))
        result = await service.submit(submission(attachment=fetcher.add(photo(1))))
        await service.set_review_message(result.verification_id, 123456789012345678)
        v = await get_verification(db, result.verification_id)
        assert v.review_message_id == 123456789012345678


class TestConstraints:
    """서비스를 거치지 않아도 DB가 하루 1회를 보장한다."""

    async def insert(self, db, status: str, interaction_id: int) -> None:
        async with db.transaction() as s:
            member_id = await s.scalar(text("SELECT id FROM member LIMIT 1"))
            await s.execute(
                text(
                    "INSERT INTO verification (member_id, interaction_id, channel_id, content,"
                    " submitted_at, workout_date, status, decided_at)"
                    " VALUES (:m, :i, 1, '내용', now(), '2026-11-04', :st, :decided)"
                ),
                {
                    "m": member_id,
                    "i": interaction_id,
                    "st": status,
                    "decided": None if status == "REVIEW_REQUIRED" else datetime.now(KST),
                },
            )

    async def test_one_active_verification_per_day(self, db, service):
        await self.insert(db, "REJECTED", 1)
        await self.insert(db, "REJECTED", 2)
        await self.insert(db, "APPROVED", 3)
        with pytest.raises(IntegrityError):
            await self.insert(db, "REVIEW_REQUIRED", 4)

    async def test_status_and_decided_at_must_match(self, db, service):
        async with db.transaction() as s:
            member_id = await s.scalar(text("SELECT id FROM member LIMIT 1"))
        with pytest.raises(IntegrityError):
            async with db.transaction() as s:
                await s.execute(
                    text(
                        "INSERT INTO verification (member_id, interaction_id, channel_id,"
                        " content, submitted_at, workout_date, status)"
                        " VALUES (:m, 1, 1, '내용', now(), '2026-11-04', 'APPROVED')"
                    ),
                    {"m": member_id},
                )

    async def test_policy_must_start_on_monday(self, db):
        with pytest.raises(IntegrityError):
            async with db.transaction() as s:
                s.add(ClubPolicy(weekly_target=3, effective_from=date(2026, 11, 4)))
