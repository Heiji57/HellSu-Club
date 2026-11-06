"""KST 기준 날짜·주간 계산. 저장은 UTC, 집계와 표시는 Asia/Seoul (ADR-0003)."""

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        raise ValueError("timezone-aware datetime이 필요합니다")
    return ts


def to_kst(ts: datetime) -> datetime:
    return _aware(ts).astimezone(KST)


def to_kst_date(ts: datetime) -> date:
    """해당 시각의 KST 날짜. 인증의 출석일(workout_date) 계산에 사용한다."""
    return to_kst(ts).date()


def week_start(d: date) -> date:
    """그 날짜가 속한 주의 월요일."""
    return d - timedelta(days=d.weekday())


@dataclass(frozen=True)
class Week:
    start: date  # 월요일 (KST)

    def __post_init__(self) -> None:
        if self.start.weekday() != 0:
            raise ValueError(f"주의 시작일은 월요일이어야 합니다: {self.start}")

    @classmethod
    def of(cls, d: date) -> "Week":
        return cls(week_start(d))

    @classmethod
    def containing(cls, ts: datetime) -> "Week":
        return cls.of(to_kst_date(ts))

    @property
    def end(self) -> date:
        """마지막 날(일요일), 포함."""
        return self.start + timedelta(days=6)

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=i) for i in range(7)]

    def bounds_utc(self) -> tuple[datetime, datetime]:
        """[월 00:00 KST, 다음 월 00:00 KST) 반열린 구간을 UTC로."""
        start = datetime.combine(self.start, time.min, tzinfo=KST)
        end = start + timedelta(days=7)
        return start.astimezone(UTC), end.astimezone(UTC)

    def previous(self) -> "Week":
        return Week(self.start - timedelta(days=7))

    def next(self) -> "Week":
        return Week(self.start + timedelta(days=7))

    def __contains__(self, d: date) -> bool:
        return self.start <= d <= self.end
