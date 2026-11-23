"""Booking length and expiry rules, without the API (decision §12, §21)."""

from datetime import datetime, timedelta, timezone as tz
from decimal import Decimal

import pytest

from bookings.availability import IST, at, expires_at_for, hour_of, hours_for, next_whole_hour
from core.errors import ApiError

from .conftest import MON, TUE, WED

NOW = datetime(2026, 11, 23, 2, 0, tzinfo=tz.utc)  # 07:30 IST


class TestHoursFor:
    def test_hours_are_the_quantity(self, tractor):
        assert hours_for(tractor.opt_hour, Decimal("3")) == 3

    def test_below_the_owners_minimum_is_refused(self, tractor):
        with pytest.raises(ApiError) as e:
            hours_for(tractor.opt_hour, Decimal("1"))  # min 2
        assert e.value.error_code == "below_min_hours"

    def test_half_hours_are_refused(self, tractor):
        with pytest.raises(ApiError) as e:
            hours_for(tractor.opt_hour, Decimal("2.5"))
        assert e.value.error_code == "validation_error"

    def test_acres_times_hours_per_acre_rounded_up(self, tractor):
        # The roadmap's case: 2.5 acres × 1.5 h = 3.75 → 4 hours.
        assert hours_for(tractor.opt_acre, Decimal("2.5")) == 4
        assert hours_for(tractor.opt_acre, Decimal("2")) == 3  # exactly 3.0 stays 3

    def test_acres_only_in_half_steps(self, tractor):
        with pytest.raises(ApiError):
            hours_for(tractor.opt_acre, Decimal("2.3"))

    def test_acres_fall_back_to_the_attachments_default(self, tractor):
        tractor.opt_acre.hours_per_acre = None  # rotavator default = 1.5
        assert hours_for(tractor.opt_acre, Decimal("1")) == 2

    def test_days_reserve_each_days_open_hours(self, tractor):
        assert hours_for(tractor.opt_day, Decimal("2")) is None
        with pytest.raises(ApiError):
            hours_for(tractor.opt_day, Decimal("1.5"))


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
