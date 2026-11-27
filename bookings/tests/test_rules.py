"""Booking length and expiry rules, without the API (decision §12, §21)."""

from datetime import datetime, timedelta, timezone as tz

import pytest

from bookings.availability import IST, at, check_hours, expires_at_for, hour_of, load_plan, next_whole_hour
from core.errors import ApiError

from .conftest import MON, TUE, WED, book, set_week

NOW = datetime(2026, 11, 23, 2, 0, tzinfo=tz.utc)  # 07:30 IST


class TestWholeHours:
    """Decision §23: renters always book whole hours, whatever the price unit (acres / days are price only)."""

    @pytest.mark.parametrize("hours", [1, 2, 12])
    def test_whole_hours(self, hours):
        assert check_hours(hours) == hours

    @pytest.mark.parametrize("hours", [0, 13, -1, 2.5])
    def test_anything_else_is_refused(self, hours):
        with pytest.raises(ApiError):
            check_hours(hours)


class TestMaxHours:
    """S11 "कितने घंटे?": from a start, until 7 PM or the next booking (keeping this job's 1-hour rest gap)."""

    def plan(self, tractor, now=None):
        set_week(tractor, 7, 19)
        return load_plan(tractor, TUE, TUE, now or at(MON, 7))

    def test_until_closing_time(self, tractor):
        p = self.plan(tractor)
        assert p.max_hours(TUE, 7) == 12 and p.max_hours(TUE, 17) == 2 and p.max_hours(TUE, 18) == 1

    def test_until_the_next_booking_minus_the_rest_gap(self, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 12, 14)  # reserves 12–15
        p = self.plan(tractor)
        assert p.max_hours(TUE, 8) == 3  # 8–11, rest 11–12, then the booking
        assert p.max_hours(TUE, 11) == 0  # 11–12 + rest would touch 12
        assert p.max_hours(TUE, 15) == 4  # after the booking's own rest hour: until 19

    def test_outside_the_day_or_past(self, tractor):
        p = self.plan(tractor, now=at(TUE, 9) + timedelta(minutes=10))
        assert p.max_hours(TUE, 6) == 0 and p.max_hours(TUE, 19) == 0
        assert p.max_hours(TUE, 9) == 0 and p.max_hours(TUE, 10) == 9  # 9 has started


class TestExpiry:
    """2 h for today or tomorrow, 6 h for 2+ days ahead, never after the start (owner, 2026-11-23)."""

    def test_today_two_hours(self):
        assert expires_at_for(at(MON, 14), NOW) == NOW + timedelta(hours=2)

    def test_today_soon_ends_at_the_start(self):
        assert expires_at_for(at(MON, 9), NOW) == at(MON, 9)  # 1.5 h away: the start comes first

    def test_tomorrow_two_hours(self):
        assert expires_at_for(at(TUE, 6), NOW) == NOW + timedelta(hours=2)

    def test_two_days_ahead_six_hours(self):
        assert expires_at_for(at(WED, 6), NOW) == NOW + timedelta(hours=6)

    def test_tomorrow_is_counted_in_ist_not_utc(self):
        # 23:00 IST Monday = 17:30 UTC Monday. At 23:00 IST, a job at 06:00 Wednesday is "2 days ahead".
        late = datetime(2026, 11, 23, 17, 30, tzinfo=tz.utc)
        assert expires_at_for(at(WED, 6), late) == late + timedelta(hours=6)
        assert expires_at_for(at(TUE, 6), late) == late + timedelta(hours=2)


class TestTime:
    def test_ist_hours_are_stored_as_utc(self):
        assert at(MON, 8) == datetime(2026, 11, 23, 2, 30, tzinfo=tz.utc)
        assert at(MON, 24) == at(TUE, 0)

    def test_midnight_ist_is_the_previous_utc_day(self):
        assert at(TUE, 0).astimezone(tz.utc) == datetime(2026, 11, 23, 18, 30, tzinfo=tz.utc)
        assert at(TUE, 0).astimezone(IST).date() == TUE

    def test_hour_of(self):
        assert hour_of(TUE, at(MON, 23)) == -1
        assert hour_of(MON, at(TUE, 1)) == 25

    def test_next_whole_hour(self):
        assert next_whole_hour(NOW) == at(MON, 8)
        assert next_whole_hour(at(MON, 8)) == at(MON, 8)

    def test_next_whole_hour_is_an_ist_hour(self):
        # 07:40 IST = 02:10 UTC. Rounding in UTC gives 03:00 UTC = 08:30 IST and loses the 8 बजे chip.
        assert next_whole_hour(datetime(2026, 11, 23, 2, 10, tzinfo=tz.utc)) == at(MON, 8)
