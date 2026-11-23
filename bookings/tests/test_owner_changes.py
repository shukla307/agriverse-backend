"""Owner changes to open hours (06 §6): one date (PUT/DELETE days/{date}/) and the weekly hours.
A change that would leave an accepted or pending booking outside the hours is refused: 409 has_bookings."""

from datetime import timedelta

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

    def test_change_hours_then_change_again(self, as_owner, tractor):
        put_day(as_owner, tractor, TUE, is_closed=False, start_hour=8, end_hour=14)
        res = put_day(as_owner, tractor, TUE, is_closed=False, start_hour=9, end_hour=13)
        assert res.json()["open"] == {"start_hour": 9, "end_hour": 13}
        assert DayOverride.objects.filter(listing=tractor).count() == 1

    def test_hours_must_make_sense(self, as_owner, tractor):
        assert put_day(as_owner, tractor, TUE, is_closed=False, start_hour=14, end_hour=8).status_code == 400
        assert put_day(as_owner, tractor, TUE, is_closed=False).status_code == 400

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

    def test_shorter_hours_that_still_fit(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 12, 14)  # its gap (14–15) may run past closing
        assert put_day(as_owner, tractor, TUE, is_closed=False, start_hour=8, end_hour=14).status_code == 200

    def test_shorter_hours_that_dont(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        assert put_day(as_owner, tractor, TUE, is_closed=False, start_hour=9, end_hour=18).status_code == 409

    def test_another_days_booking_doesnt_matter(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, WED, 8, 11)
        assert put_day(as_owner, tractor, TUE, is_closed=True).status_code == 200

    def test_undo_refused_when_weekly_hours_wouldnt_fit(self, as_owner, tractor, renter):
        DayOverride.objects.create(listing=tractor, date=TUE, start_hour=18, end_hour=22)  # an evening, once
        book(tractor, tractor.opt_hour, renter, TUE, 19, 21)
        assert del_day(as_owner, tractor, TUE).status_code == 409  # weekly hours end at 18


class TestWeeklyHoursWithBookings:
    def every_day_but(self, weekday, start=6, end=18):
        return [{"weekday": w, "start_hour": start, "end_hour": end} for w in range(7) if w != weekday]

    def test_closing_a_weekday_with_a_booking_is_refused(self, as_owner, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = put_week(as_owner, tractor, self.every_day_but(1))  # मंगल closed
        assert res.status_code == 409 and res.json()["error"]["fields"]["booking_ids"] == [b.pk]
        assert tractor.weekly_hours.count() == 7  # nothing changed

    def test_hours_that_still_fit(self, as_owner, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = put_week(as_owner, tractor, [{"weekday": w, "start_hour": 7, "end_hour": 11} for w in range(7)])
        assert res.status_code == 200

    def test_finished_bookings_dont_count(self, as_owner, tractor, renter, frozen):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        frozen.move_to("2026-11-25 00:00:00")
        assert put_week(as_owner, tractor, self.every_day_but(1)).status_code == 200

    def test_a_changed_date_keeps_its_own_hours(self, as_owner, tractor, renter):
        DayOverride.objects.create(listing=tractor, date=TUE, start_hour=6, end_hour=18)
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        assert put_week(as_owner, tractor, self.every_day_but(1)).status_code == 200
