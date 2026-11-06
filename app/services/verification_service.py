"""운동 인증 제출과 규칙 판정 (REQUIREMENTS 3, ADR-0002).

흐름:
1. 회원 확인, 오늘 이미 인증했는지 확인(다운로드 전에 빠르게 거절)
2. 입력 검증 → 이미지 다운로드·검증·해시
3. 중복 이미지 탐지 → 판정
4. 한 트랜잭션: 인증 원본 + 판정 이력 + (승인이면) 출석
5. 커밋 후 이미지 파일 저장 (실패해도 인증은 유지, 해시는 이미 저장됨)
"""

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import Clock
from app.core.database import Database
from app.core.exceptions import (
    AlreadyVerifiedToday,
    EmptySubmission,
    ImageTooLarge,
    InactiveMember,
    InvalidSubmission,
    NotAMember,
    TextTooShort,
)
from app.core.week import Week, to_kst_date
from app.image.fetcher import ImageFetcher
from app.image.hashing import hamming_distance
from app.image.processor import ProcessedImage, process_image
from app.image.storage import ImageStorage
from app.models import (
    Member,
    ReviewDecision,
    ReviewerType,
    Verification,
    VerificationReview,
    VerificationStatus,
)
from app.repositories.attendance import AttendanceRepository
from app.repositories.member import MemberRepository
from app.repositories.verification import VerificationRef, VerificationRepository
from app.services.attendance_service import AttendanceService
from app.services.policy_service import target_for_week

log = logging.getLogger(__name__)

MAX_CONTENT_LENGTH = 1000
MAX_MATCHES_KEPT = 10

# 동시 제출 등으로 하루 1회 제약에 걸린 경우 "이미 인증"으로 응답한다.
_ALREADY_VERIFIED_CONSTRAINTS = frozenset(
    {
        "uq_verification_member_day_active",
        "uq_attendance_member_day_active",
        "uq_verification_interaction_id",
    }
)


# --- 입력과 결과 ---


@dataclass(frozen=True)
class AttachmentInfo:
    """Discord 첨부파일에서 필요한 값만. 서비스가 Discord 타입에 의존하지 않게 한다."""

    url: str
    filename: str
    size: int


@dataclass(frozen=True)
class Submission:
    discord_user_id: int
    interaction_id: int
    channel_id: int
    submitted_at: datetime  # interaction 생성 시각 (ADR-0003)
    content: str | None = None
    attachment: AttachmentInfo | None = None


@dataclass(frozen=True)
class DuplicateMatch:
    verification_id: int
    distance: int  # 0이면 SHA-256 완전 일치
    exact: bool
    ref: VerificationRef | None = None


@dataclass(frozen=True)
class RuleDecision:
    status: VerificationStatus
    decision: ReviewDecision
    reason: str
    matches: tuple[DuplicateMatch, ...] = ()

    @property
    def details(self) -> dict[str, Any]:
        return {
            "matches": [
                {"verification_id": m.verification_id, "distance": m.distance, "exact": m.exact}
                for m in self.matches
            ]
        }


@dataclass(frozen=True)
class SubmissionResult:
    verification_id: int
    status: VerificationStatus
    workout_date: date
    reason: str
    content: str | None
    week_count: int
    week_target: int
    matches: tuple[DuplicateMatch, ...] = ()
    image_file: Path | None = None  # 저장된 이미지 파일(관리자 채널 게시용)
    image_ext: str | None = None

    @property
    def approved(self) -> bool:
        return self.status is VerificationStatus.APPROVED

    @property
    def just_achieved(self) -> bool:
        """이번 인증으로 주간 목표에 막 도달했는지."""
        return self.approved and self.week_count == self.week_target


# --- 순수 규칙 (단위 테스트 대상) ---


def normalize_content(content: str | None) -> str | None:
    if content is None:
        return None
    content = content.strip()
    return content or None


def validate_input(content: str | None, *, has_image: bool, text_min_length: int) -> None:
    if content is None and not has_image:
        raise EmptySubmission()
    if content is not None and len(content) > MAX_CONTENT_LENGTH:
        raise InvalidSubmission(f"운동 내용은 {MAX_CONTENT_LENGTH}자 이하로 적어 주세요.")
    if content is not None and not has_image:
        length = len("".join(content.split()))
        if length < text_min_length:
            raise TextTooShort(
                f"운동 내용을 공백 제외 {text_min_length}자 이상 적어 주세요(지금 {length}자). "
                "사진을 함께 올리면 짧아도 됩니다."
            )


def find_duplicates(
    sha256: str,
    phash: str,
    *,
    sha_matches: list[int],
    candidates: list[tuple[int, str]],
    threshold: int,
) -> tuple[DuplicateMatch, ...]:
    """SHA-256 완전 일치 + pHash 해밍 거리 ≤ threshold. 가까운 순으로 최대 10건."""
    exact = set(sha_matches)
    matches = [DuplicateMatch(vid, 0, True) for vid in sorted(exact)]
    similar = [
        DuplicateMatch(vid, d, False)
        for vid, other in candidates
        if vid not in exact and (d := hamming_distance(phash, other)) <= threshold
    ]
    similar.sort(key=lambda m: (m.distance, m.verification_id))
    return tuple((matches + similar)[:MAX_MATCHES_KEPT])


def decide(matches: tuple[DuplicateMatch, ...], *, has_image: bool) -> RuleDecision:
    if matches:
        top = matches[0]
        if top.exact:
            reason = f"이전에 제출된 사진과 같은 파일입니다 (인증 #{top.verification_id})."
        else:
            reason = (
                f"이전에 제출된 사진과 매우 비슷합니다 "
                f"(인증 #{top.verification_id}, 차이 {top.distance})."
            )
        return RuleDecision(
            VerificationStatus.REVIEW_REQUIRED, ReviewDecision.REVIEW, reason, matches
        )
    reason = "규칙 통과: 중복 사진 없음" if has_image else "규칙 통과: 텍스트 인증"
    return RuleDecision(VerificationStatus.APPROVED, ReviewDecision.APPROVE, reason)


# --- 서비스 ---


class VerificationService:
    def __init__(
        self,
        db: Database,
        clock: Clock,
        *,
        attendance: AttendanceService,
        fetcher: ImageFetcher,
        storage: ImageStorage,
        text_min_length: int,
        image_max_bytes: int,
        phash_threshold: int,
        default_weekly_target: int,
    ) -> None:
        self._db = db
        self._clock = clock
        self._attendance = attendance
        self._fetcher = fetcher
        self._storage = storage
        self._text_min_length = text_min_length
        self._image_max_bytes = image_max_bytes
        self._phash_threshold = phash_threshold
        self._default_target = default_weekly_target

    async def submit(self, sub: Submission) -> SubmissionResult:
        content = normalize_content(sub.content)
        workout_date = to_kst_date(sub.submitted_at)

        # 1. 다운로드 전에 빠르게 거절할 수 있는 것들
        async with self._db.transaction() as s:
            member = await self._require_active_member(s, sub.discord_user_id)
            if await VerificationRepository(s).exists_active_on(member.id, workout_date):
                raise AlreadyVerifiedToday()

        # 2. 입력 검증과 이미지 처리 (트랜잭션 밖)
        validate_input(
            content, has_image=sub.attachment is not None, text_min_length=self._text_min_length
        )
        image = await self._load_image(sub.attachment) if sub.attachment else None

        # 3~4. 판정과 저장
        try:
            async with self._db.transaction() as s:
                result = await self._record(s, sub, content, workout_date, image)
        except IntegrityError as e:
            if _constraint_name(e) in _ALREADY_VERIFIED_CONSTRAINTS:
                raise AlreadyVerifiedToday() from e
            raise

        # 5. 이미지 파일 저장
        if image is not None:
            result = await self._store_image(result, sub.submitted_at, image)

        log.info(
            "verification #%d member=%s %s (%s)",
            result.verification_id,
            sub.discord_user_id,
            result.status,
            result.reason,
        )
        return result

    async def set_review_message(self, verification_id: int, message_id: int) -> None:
        async with self._db.transaction() as s:
            await VerificationRepository(s).set_review_message(verification_id, message_id)

    async def get(self, verification_id: int) -> Verification | None:
        async with self._db.transaction() as s:
            return await VerificationRepository(s).get(verification_id)

    # --- 내부 ---

    async def _require_active_member(self, s: AsyncSession, discord_user_id: int) -> Member:
        member = await MemberRepository(s).get_by_discord_id(discord_user_id)
        if member is None:
            raise NotAMember()
        if not member.is_active:
            raise InactiveMember()
        return member

    async def _load_image(self, attachment: AttachmentInfo) -> ProcessedImage:
        # 메타데이터의 크기가 이미 넘으면 받지 않는다. 실제 크기는 다운로드 중에 다시 확인한다.
        if attachment.size > self._image_max_bytes:
            raise ImageTooLarge.for_limit(self._image_max_bytes)
        raw = await self._fetcher.fetch(attachment.url, max_bytes=self._image_max_bytes)
        if len(raw) > self._image_max_bytes:
            raise ImageTooLarge.for_limit(self._image_max_bytes)
        return await asyncio.to_thread(process_image, raw)

    async def _record(
        self,
        s: AsyncSession,
        sub: Submission,
        content: str | None,
        workout_date: date,
        image: ProcessedImage | None,
    ) -> SubmissionResult:
        member = await self._require_active_member(s, sub.discord_user_id)
        repo = VerificationRepository(s)

        matches: tuple[DuplicateMatch, ...] = ()
        if image is not None:
            matches = find_duplicates(
                image.sha256,
                image.phash,
                sha_matches=list(await repo.ids_with_sha256(image.sha256)),
                candidates=list(await repo.all_phashes()),
                threshold=self._phash_threshold,
            )
        decision = decide(matches, has_image=image is not None)

        now = self._clock.now()
        decided = decision.status is not VerificationStatus.REVIEW_REQUIRED
        verification = await repo.add(
            Verification(
                member_id=member.id,
                interaction_id=sub.interaction_id,
                channel_id=sub.channel_id,
                content=content,
                submitted_at=sub.submitted_at,
                workout_date=workout_date,
                image_mime=image.mime if image else None,
                image_bytes=image.original_bytes if image else None,
                image_sha256=image.sha256 if image else None,
                image_phash=image.phash if image else None,
                status=decision.status,
                decided_at=now if decided else None,
                # 규칙 판정 결과는 이 응답에서 바로 보여주므로 알림 완료로 기록한다 (ADR-0005)
                result_notified_at=now if decided else None,
            )
        )
        await repo.add_review(
            VerificationReview(
                verification_id=verification.id,
                reviewer_type=ReviewerType.RULE,
                decision=decision.decision,
                reason=decision.reason,
                details=decision.details,
            )
        )
        if decision.status is VerificationStatus.APPROVED:
            await self._attendance.create(s, verification)

        refs = await repo.refs(m.verification_id for m in matches)
        week = Week.of(workout_date)
        return SubmissionResult(
            verification_id=verification.id,
            status=decision.status,
            workout_date=workout_date,
            reason=decision.reason,
            content=content,
            week_count=await AttendanceRepository(s).count_active_in_week(member.id, week),
            week_target=await target_for_week(s, week, self._default_target),
            matches=tuple(
                DuplicateMatch(m.verification_id, m.distance, m.exact, refs.get(m.verification_id))
                for m in matches
            ),
        )

    async def _store_image(
        self, result: SubmissionResult, submitted_at: datetime, image: ProcessedImage
    ) -> SubmissionResult:
        relative = ImageStorage.relative_path(result.verification_id, submitted_at, image.ext)
        try:
            path = await self._storage.save(relative, image.data)
        except Exception:
            # 인증과 해시는 이미 저장됐으므로 중복 탐지는 계속 동작한다.
            log.warning("인증 #%d 이미지 저장 실패", result.verification_id, exc_info=True)
            return result
        try:
            async with self._db.transaction() as s:
                await VerificationRepository(s).set_image_path(result.verification_id, relative)
        except Exception:
            log.warning("인증 #%d 이미지 경로 기록 실패", result.verification_id, exc_info=True)
            await self._storage.delete(relative)  # 경로가 없는 고아 파일을 남기지 않는다
            return result
        return replace(result, image_file=path, image_ext=image.ext)


def _constraint_name(error: IntegrityError) -> str | None:
    # asyncpg 예외는 SQLAlchemy 어댑터의 __cause__에 있다.
    for candidate in (error.orig, getattr(error.orig, "__cause__", None)):
        name = getattr(candidate, "constraint_name", None)
        if name:
            return name
    return None
