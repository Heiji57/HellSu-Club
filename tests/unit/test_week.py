from datetime import UTC, date, datetime, timedelta

import pytest

from app.core.clock import FixedClock
from app.core.week import KST, Week, to_kst_date, week_start


def kst(*args) -> datetime:
    return datetime(*args, tzinfo=KST)


class TestToKstDate:
    def test_utc_afternoon_is_next_day_in_kst(self):
        # 2026-11-08(일) 15:00 UTC = 2026-11-09(월) 00:00 KST
        assert to_kst_date(datetime(2026, 11, 8, 15, 0, tzinfo=UTC)) == date(2026, 11, 9)

    def test_one_second_before_midnight_kst(self):
        assert to_kst_date(datetime(2026, 11, 8, 14, 59, 59, tzinfo=UTC)) == date(2026, 11, 8)

    def test_naive_datetime_rejected(self):
        with pytest.raises(ValueError):
            to_kst_date(datetime(2026, 11, 8, 12, 0))


class TestWeek:
    def test_week_start_is_monday(self):
        for offset in range(7):
            d = date(2026, 11, 2) + timedelta(days=offset)  # 11/2(월) ~ 11/8(일)
            assert week_start(d) == date(2026, 11, 2)

    def test_non_monday_start_rejected(self):
        with pytest.raises(ValueError):
            Week(date(2026, 11, 3))

    def test_sunday_235959_kst_belongs_to_that_week(self):
        assert Week.containing(kst(2026, 11, 8, 23, 59, 59)).start == date(2026, 11, 2)

    def test_monday_000000_kst_starts_new_week(self):
        assert Week.containing(kst(2026, 11, 9, 0, 0, 0)).start == date(2026, 11, 9)

    def test_bounds_utc_half_open(self):
        start, end = Week(date(2026, 11, 2)).bounds_utc()
        assert start == datetime(2026, 11, 1, 15, 0, tzinfo=UTC)
        assert end == datetime(2026, 11, 8, 15, 0, tzinfo=UTC)
        assert end - start == timedelta(days=7)

    def test_end_and_days(self):
        w = Week(date(2026, 11, 2))
        assert w.end == date(2026, 11, 8)
        assert w.days[0] == date(2026, 11, 2) and w.days[-1] == date(2026, 11, 8)
        assert date(2026, 11, 8) in w and date(2026, 11, 9) not in w

    def test_previous_and_next_across_year(self):
        w = Week(date(2026, 12, 28))
        assert w.next().start == date(2027, 1, 4)
        assert w.next().previous() == w

    def test_week_spanning_new_year(self):
        assert Week.of(date(2027, 1, 1)).start == date(2026, 12, 28)


class TestFixedClock:
    def test_normalizes_to_utc_and_advances(self):
        c = FixedClock(kst(2026, 11, 9, 9, 0))
        assert c.now() == datetime(2026, 11, 9, 0, 0, tzinfo=UTC)
        assert c.now().tzinfo == UTC
        c.advance(timedelta(hours=1))
        assert c.now() == datetime(2026, 11, 9, 1, 0, tzinfo=UTC)

    def test_rejects_naive(self):
        with pytest.raises(ValueError):
            FixedClock(datetime(2026, 11, 9))
