"""GET /api/listings/{id}/start-times/?date: the start hours with at least one free hour, and how long a job
from each may last (decision §23, 05 §4). The machine is open 7–19 (the fixed hours), gap 1 hour."""

from datetime import timedelta

import pytest

from bookings.availability import at
from bookings.models import Booking
from listings.models import DayOverride

from .conftest import MON, TUE, WED, book, set_week, starts


@pytest.fixture(autouse=True)
def seven_to_seven(tractor):
    set_week(tractor, 7, 19)


def answer(res):
    assert res.status_code == 200, res.json()
    j = res.json()
    return j["start_hours"], {int(k): v for k, v in j["max_hours"].items()}


class TestAFreeDay:
    def test_every_hour_7_to_6_and_each_runs_until_7_pm(self, as_renter, tractor):
        hours, longest = answer(starts(as_renter, tractor, TUE))
        assert hours == list(range(7, 19))  # शाम 6 बजे is the last start: 1 hour until 7
        assert longest == {s: 19 - s for s in range(7, 19)}


class TestGap:
    def test_a_booking_and_its_rest_hour_are_not_offered(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 12, 14)  # reserves 12–15 (1-hour rest gap)
        hours, longest = answer(starts(as_renter, tractor, TUE))
        assert hours == [7, 8, 9, 10, 15, 16, 17, 18]  # 11 + 1 h rest would touch 12
        assert longest[7] == 4 and longest[10] == 1  # must end (with its own rest) by 12
        assert longest[15] == 4  # after the booking's rest hour: until 7 PM

    def test_no_gap(self, as_renter, tractor, renter):
        tractor.gap_hours = 0
        tractor.save()
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, gap=0)
        hours, longest = answer(starts(as_renter, tractor, TUE))
        assert hours[:2] == [7, 11] and longest[7] == 1


class TestOneMachine:
    def test_any_price_of_the_machine_holds_it(self, as_renter, tractor, renter):
        # Tractor + cultivator 8–11 holds the tractor: nothing else may use it then (one machine, one place).
        book(tractor, tractor.opt_cult, renter, TUE, 8, 11)
        hours, _ = answer(starts(as_renter, tractor, TUE))
        assert 8 not in hours and 10 not in hours and 12 in hours


class TestWhichBookingsCount:
    def test_pending_request_holds_the_time(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED, expires_at=at(MON, 10))
        assert 8 not in answer(starts(as_renter, tractor, TUE))[0]

    def test_expired_request_does_not(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED, expires_at=at(MON, 7))
        assert 8 in answer(starts(as_renter, tractor, TUE))[0]

    def test_rejected_and_cancelled_free_the_time(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REJECTED)
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.CANCELLED_BY_OWNER)
        assert 8 in answer(starts(as_renter, tractor, TUE))[0]


class TestToday:
    def test_only_from_the_next_whole_hour(self, as_renter, tractor):
        assert answer(starts(as_renter, tractor, MON))[0][0] == 8  # now = 07:30

    def test_at_twenty_to_eight_eight_is_still_offered(self, as_renter, tractor, frozen):
        frozen.move_to("2026-11-23 02:10:00")  # 07:40 IST
        assert answer(starts(as_renter, tractor, MON))[0][0] == 8

    def test_after_six_pm_nothing_is_left(self, as_renter, tractor, frozen):
        frozen.move_to("2026-11-23 12:40:00")  # 18:10 IST
        res = starts(as_renter, tractor, MON)
        assert answer(res)[0] == [] and res.json()["next_free_date"] == TUE.isoformat()


class TestClosed:
    def test_closed_date_suggests_the_next_free_one(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=TUE, is_closed=True)
        res = starts(as_renter, tractor, TUE)
        assert answer(res)[0] == [] and res.json()["next_free_date"] == WED.isoformat()

    def test_fully_booked_day(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 7, 19)
        res = starts(as_renter, tractor, TUE)
        assert answer(res)[0] == [] and res.json()["next_free_date"] == WED.isoformat()

    def test_no_weekly_hours_is_not_bookable(self, as_renter, tractor):
        tractor.has_schedule = False
        tractor.save()
        res = starts(as_renter, tractor, TUE)
        assert res.status_code == 409 and res.json()["error"]["code"] == "not_bookable"

    def test_paused_is_not_bookable(self, as_renter, tractor):
        tractor.status = "paused"
        tractor.save()
        assert starts(as_renter, tractor, TUE).status_code == 409


class TestMidnight:
    """IST ↔ UTC around midnight (hours written straight to the database: the API only allows 7–19)."""

    def test_gap_after_a_late_job_spills_into_the_next_day(self, as_renter, tractor, renter):
        set_week(tractor, start=0, end=24)
        b = book(tractor, tractor.opt_hour, renter, TUE, 22, 24)  # reserves until Wed 01:00 IST
        b.refresh_from_db()  # what the database stored: 22:00 IST = 16:30 UTC
        assert b.start_at.isoformat() == "2026-11-24T16:30:00+00:00"
        assert answer(starts(as_renter, tractor, WED))[0][0] == 1  # 0 would touch the gap


class TestRequestChecks:
    def test_date_outside_the_30_days(self, as_renter, tractor):
        assert starts(as_renter, tractor, MON + timedelta(days=30)).status_code == 400
        assert starts(as_renter, tractor, MON - timedelta(days=1)).status_code == 400

    def test_removed_listing(self, as_renter, tractor):
        tractor.status = "removed"
        tractor.save()
        assert starts(as_renter, tractor, TUE).status_code == 404

    def test_login_needed(self, api, tractor):
        assert starts(api, tractor, TUE).status_code == 401
