from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """현재 시각(UTC, tz-aware)."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """테스트용 시계. `set()`이나 `advance()`로 시각을 옮긴다."""

    def __init__(self, at: datetime) -> None:
        self._at = _require_aware(at)

    def now(self) -> datetime:
        return self._at

    def set(self, at: datetime) -> None:
        self._at = _require_aware(at)

    def advance(self, delta: timedelta) -> None:
        self._at = self._at + delta


def _require_aware(at: datetime) -> datetime:
    if at.tzinfo is None:
        raise ValueError("timezone-aware datetime이 필요합니다")
    return at.astimezone(UTC)
