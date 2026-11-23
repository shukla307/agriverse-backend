"""GET /api/listings/{id}/start-times/: only the start times that are really free (05 §4)."""

from datetime import timedelta

from bookings.availability import at
from bookings.models import Booking
from listings.models import DayOverride

from .conftest import MON, TUE, WED, book, set_week, starts


def hours_list(res):
    assert res.status_code == 200, res.json()
    return res.json()["start_hours"]


class TestGap:
    def test_one_hour_gap(self, as_renter, tractor, renter):
        # The roadmap's case: 8–11 with a 1-hour gap reserves 8–12. A 3-hour job can start at 12, not 11.
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 3))
        assert got == [12, 13, 14, 15]
        # Before it: a job must finish (with its own gap) by 8 → start ≤ 4, but the day opens at 6.
        assert 6 not in got and 11 not in got

    def test_one_hour_gap_before_a_booking(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 12, 14)
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2))
        # 2 h + 1 h gap must end by 12 → start ≤ 9. After: 14–15 is the gap → start ≥ 15.
        assert got == [6, 7, 8, 9, 15, 16]

    def test_no_gap(self, as_renter, tractor, renter):
        tractor.gap_hours = 0
        tractor.save()
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, gap=0)
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 3))
        assert got[0] == 11  # 8–11 then 11–14 is fine with no gap
        assert 6 not in got  # 6–9 overlaps

    def test_a_job_may_end_at_closing_time(self, as_renter, tractor):
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 3))
        assert got[-1] == 15  # 15–18, the gap runs past closing


class TestOneMachine:
    def test_another_option_on_the_same_machine_still_clashes(self, as_renter, tractor, renter):
        # Tractor + cultivator 8–11 holds the tractor: tractor + rotavator can't use it then either.
        book(tractor, tractor.opt_cult, renter, TUE, 8, 11)
        got = hours_list(starts(as_renter, tractor, tractor.opt_acre, TUE, 2))  # 2 acres × 1.5 = 3 h
        assert got == [12, 13, 14, 15]

    def test_acres_become_hours(self, as_renter, tractor):
        res = starts(as_renter, tractor, tractor.opt_acre, TUE, "2.5")
        assert res.json()["hours"] == 4
        assert res.json()["start_hours"][-1] == 14  # 14–18


class TestWhichBookingsCount:
    def test_pending_request_holds_the_time(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED,
             expires_at=at(MON, 10))
        assert 8 not in hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2))

    def test_expired_request_does_not(self, as_renter, tractor, renter):
        # Not yet marked expired by the job, but past expires_at (07:00 < now 07:30): the time is free.
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED,
             expires_at=at(MON, 7))
        assert 8 in hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2))

    def test_rejected_and_cancelled_free_the_time(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REJECTED)
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.CANCELLED_BY_OWNER)
        assert 8 in hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2))


class TestOpenHours:
    def test_today_at_twenty_to_eight_still_offers_eight(self, as_renter, tractor, frozen):
        frozen.move_to("2026-11-23 02:10:00")  # 07:40 IST
        assert hours_list(starts(as_renter, tractor, tractor.opt_hour, MON, 2))[0] == 8

    def test_today_only_from_the_next_whole_hour(self, as_renter, tractor):
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, MON, 2))  # now = 07:30
        assert got[0] == 8

    def test_closed_weekday_suggests_the_next_free_date(self, as_renter, tractor):
        set_week(tractor, weekdays=[0, 2, 3, 4, 5, 6])  # Tuesday (मंगल) closed
        res = starts(as_renter, tractor, tractor.opt_hour, TUE, 2)
        assert res.json()["start_hours"] == []
        assert res.json()["next_free_date"] == WED.isoformat()

    def test_closed_date(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=TUE, is_closed=True)
        assert hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2)) == []

    def test_changed_hours_on_one_date(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=TUE, start_hour=8, end_hour=12)
        assert hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2)) == [8, 9, 10]

    def test_job_longer_than_the_day(self, as_renter, tractor):
        res = starts(as_renter, tractor, tractor.opt_acre, TUE, 10)  # 15 hours > 12 open
        assert res.json()["start_hours"] == [] and res.json()["next_free_date"] is None

    def test_no_weekly_hours_is_not_bookable(self, as_renter, tractor):
        tractor.has_schedule = False
        tractor.save()
        res = starts(as_renter, tractor, tractor.opt_hour, TUE, 2)
        assert res.status_code == 409 and res.json()["error"]["code"] == "not_bookable"

    def test_paused_is_not_bookable(self, as_renter, tractor):
        tractor.status = "paused"
        tractor.save()
        assert starts(as_renter, tractor, tractor.opt_hour, TUE, 2).status_code == 409


class TestMidnight:
    """IST ↔ UTC around midnight: IST midnight is 18:30 UTC the day before."""

    def test_gap_after_a_late_job_spills_into_the_next_day(self, as_renter, tractor, renter):
        set_week(tractor, start=0, end=24)
        b = book(tractor, tractor.opt_hour, renter, TUE, 22, 24)  # reserves until Wed 01:00 IST
        b.refresh_from_db()  # what the database stored: 22:00 IST = 16:30 UTC
        assert b.start_at.isoformat() == "2026-11-24T16:30:00+00:00"
        got = hours_list(starts(as_renter, tractor, tractor.opt_hour, WED, 2))
        assert got[0] == 1  # 0 would touch the gap

    def test_a_job_can_run_until_midnight(self, as_renter, tractor):
        set_week(tractor, start=18, end=24)
        assert hours_list(starts(as_renter, tractor, tractor.opt_hour, TUE, 2))[-1] == 22


class TestPerDay:
    def test_free_days(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, WED, 15, 17)
        DayOverride.objects.create(listing=tractor, date=WED + timedelta(days=1), is_closed=True)
        res = starts(as_renter, tractor, tractor.opt_day, MON, 1)
        days = res.json()["days_free"]
        assert MON.isoformat() not in days  # it opened at 6, now is 7:30
        assert TUE.isoformat() in days
        assert WED.isoformat() not in days  # has a booking
        assert (WED + timedelta(days=1)).isoformat() not in days  # closed
        assert days[-1] == (MON + timedelta(days=29)).isoformat()  # 30 days, no more

    def test_the_gap_before_a_day_counts(self, as_renter, tractor, renter):
        set_week(tractor, start=0, end=24)
        book(tractor, tractor.opt_hour, renter, TUE, 22, 24)  # gap runs to Wed 01:00
        days = starts(as_renter, tractor, tractor.opt_day, TUE, 1).json()["days_free"]
        assert TUE.isoformat() not in days and WED.isoformat() not in days


class TestRequestChecks:
    def test_below_min_hours(self, as_renter, tractor):
        res = starts(as_renter, tractor, tractor.opt_hour, TUE, 1)
        assert res.status_code == 400 and res.json()["error"]["code"] == "below_min_hours"

    def test_date_outside_the_30_days(self, as_renter, tractor):
        assert starts(as_renter, tractor, tractor.opt_hour, MON + timedelta(days=30), 2).status_code == 400
        assert starts(as_renter, tractor, tractor.opt_hour, MON - timedelta(days=1), 2).status_code == 400

    def test_option_of_another_listing(self, as_renter, tractor, owner):
        other = tractor.opt_hour
        other.is_active = False
        other.save()
        res = starts(as_renter, tractor, other, TUE, 2)  # a removed price can't be booked
        assert res.status_code == 400 and res.json()["error"]["fields"]["option_id"] == "unknown"

    def test_removed_listing(self, as_renter, tractor):
        tractor.status = "removed"
        tractor.save()
        assert starts(as_renter, tractor, tractor.opt_hour, TUE, 2).status_code == 404

    def test_login_needed(self, api, tractor):
        assert starts(api, tractor, tractor.opt_hour, TUE, 2).status_code == 401
