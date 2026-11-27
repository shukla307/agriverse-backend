"""Owner changes to open hours (06 §6). Decision §23 (owner, 2026-11-27): every machine is open every day 7–19, so
the only change left is CLOSING one date (slice 8) and undoing it; other hours are 400 fixed_hours.
Closing a date with an accepted or pending booking is refused: 409 has_bookings."""

from datetime import timedelta

import pytest

from bookings.availability import at
from bookings.models import Booking
from listings.models import DayOverride

from .conftest import MON, TUE, WED, book


def put_day(client, listing, d, **body):
    return client.put(f"/api/listings/{listing.pk}/days/{d.isoformat()}/", body, format="json")


def del_day(client, listing, d):
    return client.delete(f"/api/listings/{listing.pk}/days/{d.isoformat()}/")


def put_week(client, listing, days):
    return client.put(f"/api/listings/{listing.pk}/weekly-hours/", {"days": days}, format="json")


class TestOneDate:
    def test_close_and_undo(self, as_owner, tractor):
        res = put_day(as_owner, tractor, TUE, is_closed=True)
        assert res.status_code == 200, res.json()
        assert res.json()["status"] == "closed" and res.json()["override"] is True
        assert DayOverride.objects.get(listing=tractor, date=TUE).is_closed
        assert del_day(as_owner, tractor, TUE).status_code == 204
        assert not DayOverride.objects.filter(listing=tractor).exists()

    @pytest.mark.parametrize("hours", [dict(start_hour=8, end_hour=14), dict(start_hour=7, end_hour=19), {}])
    def test_other_hours_for_one_date_are_refused(self, as_owner, tractor, hours):
        res = put_day(as_owner, tractor, TUE, is_closed=False, **hours)
        assert res.status_code == 400 and res.json()["error"]["code"] == "fixed_hours"
        assert not DayOverride.objects.exists()

    def test_closed_ignores_hours(self, as_owner, tractor):
        put_day(as_owner, tractor, TUE, is_closed=True, start_hour=8, end_hour=14)
        o = DayOverride.objects.get(listing=tractor, date=TUE)
        assert o.start_hour is None and o.end_hour is None

    def test_only_within_30_days(self, as_owner, tractor):
        assert put_day(as_owner, tractor, MON + timedelta(days=30), is_closed=True).status_code == 400
        assert put_day(as_owner, tractor, MON - timedelta(days=1), is_closed=True).status_code == 400

    def test_someone_elses_machine(self, as_renter, tractor):
        assert put_day(as_renter, tractor, TUE, is_closed=True).status_code == 404

    def test_needs_weekly_hours_first(self, as_owner, tractor):
        tractor.has_schedule = False
        tractor.save()
        assert put_day(as_owner, tractor, TUE, is_closed=True).status_code == 409


class TestDateWithBookings:
    def test_cant_close_a_day_with_an_accepted_booking(self, as_owner, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = put_day(as_owner, tractor, TUE, is_closed=True)
        assert res.status_code == 409
        assert res.json()["error"] == {"code": "has_bookings", "fields": {"booking_ids": [b.pk]}}
        assert not DayOverride.objects.exists()

    def test_cant_close_a_day_with_a_pending_request(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED, expires_at=at(MON, 10))
        assert put_day(as_owner, tractor, TUE, is_closed=True).status_code == 409

    def test_an_expired_request_doesnt_stop_it(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED, expires_at=at(MON, 7))
        assert put_day(as_owner, tractor, TUE, is_closed=True).status_code == 200

    def test_another_days_booking_doesnt_matter(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, WED, 8, 11)
        assert put_day(as_owner, tractor, TUE, is_closed=True).status_code == 200

    def test_undo_refused_when_weekly_hours_wouldnt_fit(self, as_owner, tractor, renter):
        DayOverride.objects.create(listing=tractor, date=TUE, start_hour=18, end_hour=22)  # an evening, once
        book(tractor, tractor.opt_hour, renter, TUE, 19, 21)
        assert del_day(as_owner, tractor, TUE).status_code == 409  # weekly hours end at 18


class TestFixedWeek:
    """PUT weekly-hours/ accepts only every day 7–19 (an old app may still send it); anything else: fixed_hours."""

    def week(self, start=7, end=19, days=range(7)):
        return [{"weekday": w, "start_hour": start, "end_hour": end} for w in days]

    @pytest.mark.parametrize("days", [
        dict(start=0, end=24),  # the bug: "रात 12 बजे से रात 12 बजे तक"
        dict(start=6, end=18),
        dict(days=range(6)),  # रवि closed
    ])
    def test_anything_but_every_day_7_to_19_is_refused(self, as_owner, tractor, days):
        before = list(tractor.weekly_hours.values_list("weekday", "start_hour", "end_hour"))
        res = put_week(as_owner, tractor, self.week(**days))
        assert res.status_code == 400 and res.json()["error"]["code"] == "fixed_hours"
        assert list(tractor.weekly_hours.values_list("weekday", "start_hour", "end_hour")) == before

    def test_every_day_7_to_19_is_accepted(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = put_week(as_owner, tractor, self.week())
        assert res.status_code == 200
        assert {(d["start_hour"], d["end_hour"]) for d in res.json()["days"]} == {(7, 19)}
