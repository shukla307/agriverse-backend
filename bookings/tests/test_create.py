"""POST /api/bookings/, POST /api/bookings/quote/, GET /api/bookings/{id}/ (Phase 5 slice 3, 05 §5, 06 §8).

Clock: Monday 2026-11-23 07:30 IST (conftest). Ramesh's tractor: every day 6–18, gap 1 hour.
"""

import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from rest_framework.test import APIClient

from accounts.models import Address
from bookings import requests as booking_requests
from bookings.availability import Plan, at
from bookings.models import Booking, BookingEvent
from catalog.models import JobCategory
from core.errors import ApiError
from listings.models import DayOverride, Listing

from .conftest import MON, TUE, WED, book, set_week, starts

URL = "/api/bookings/"


def body(option, day, hour, quantity, **extra):
    return {
        "option_id": option.pk, "start_at": at(day, hour).isoformat(), "quantity": str(quantity),
        "job": "land_prep", "field": {"lat": "25.51", "lng": "81.86", "village": "सोरांव", "landmark": "पुल के पास"},
        **extra,
    }


def send(client, *args, **extra):
    return client.post(URL, body(*args, **extra), format="json")


def code(res):
    return res.json()["error"]["code"]


class TestSendARequest:
    def test_hours(self, as_renter, tractor, renter):
        res = send(as_renter, tractor.opt_hour, TUE, 8, 3)
        assert res.status_code == 201, res.json()
        j = res.json()
        assert j["status"] == "requested"
        assert j["start_at"] == "2026-11-24T08:00:00+05:30"
        assert j["end_at"] == "2026-11-24T11:00:00+05:30"
        assert j["hours"] == 3 and j["estimated_total"] == "1800.00"
        b = Booking.objects.get(pk=j["id"])
        assert b.blocked_until == at(TUE, 12)  # work + the owner's 1-hour gap
        assert b.renter == renter and b.field_village == "सोरांव"
        assert list(b.events.values_list("to_status", flat=True)) == ["requested"]

    def test_acres_are_rounded_up_to_whole_hours(self, as_renter, tractor):
        res = send(as_renter, tractor.opt_acre, TUE, 8, "2.5")  # 2.5 × 1.5 = 3.75 → 4 hours
        j = res.json()
        assert res.status_code == 201, j
        assert j["hours"] == 4 and j["end_at"] == "2026-11-24T12:00:00+05:30"
        assert j["estimated_total"] == "3750.00" and j["option"]["hours_per_acre"] == "1.5"

    def test_negotiable_has_no_estimate(self, as_renter, tractor):
        tractor.opt_hour.price_unit, tractor.opt_hour.price = "negotiable", None
        tractor.opt_hour.save()
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2)
        assert res.status_code == 201, res.json()
        assert res.json()["estimated_total"] is None

    def test_days_one_row_per_day_with_that_days_hours(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=WED, is_closed=False, start_hour=8, end_hour=14)
        res = send(as_renter, tractor.opt_day, TUE, 6, 2)
        j = res.json()
        assert res.status_code == 201, j
        rows = Booking.objects.filter(group_id=j["group_id"]).order_by("start_at")
        assert [(r.start_at, r.end_at) for r in rows] == [(at(TUE, 6), at(TUE, 18)), (at(WED, 8), at(WED, 14))]
        assert [d["start_at"][:10] for d in j["days"]] == ["2026-11-24", "2026-11-25"]
        assert j["quantity"] == "2" and j["estimated_total"] == "10000.00"
        assert len({r.expires_at for r in rows}) == 1  # the group is answered together

    def test_a_closed_day_inside_the_days_is_refused(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=WED, is_closed=True)
        assert code(send(as_renter, tractor.opt_day, TUE, 6, 2)) == "outside_hours"

    def test_job_other_needs_a_name(self, as_renter, tractor):
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2, job="other")
        assert res.status_code == 400 and "job_other" in res.json()["error"]["fields"]
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2, job="other", job_other="  मेड़ बनाना ")
        assert res.status_code == 201 and res.json()["job_other"] == "मेड़ बनाना"

    def test_a_saved_place_is_copied(self, as_renter, tractor, renter):
        field = Address.objects.create(user=renter, label="field", lat="25.52", lng="81.87", village="बहरिया",
                                       landmark="नहर के पास")
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2, field={"address_id": field.pk})
        assert res.status_code == 201, res.json()
        b = Booking.objects.get(pk=res.json()["id"])
        assert (str(b.field_lat), b.field_village, b.field_landmark) == ("25.520000", "बहरिया", "नहर के पास")

    def test_someone_elses_place_is_refused(self, as_renter, tractor, owner):
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2, field={"address_id": owner.home_address_id})
        assert res.status_code == 400

    def test_field_outside_india_is_refused(self, as_renter, tractor):
        res = send(as_renter, tractor.opt_hour, TUE, 8, 2, field={"lat": "51.5", "lng": "0.1"})
        assert res.status_code == 400


class TestExpiryTime:
    """Decision §21: 2 h for today / tomorrow, 6 h for 2+ days ahead, never after the start."""

    def expires(self, client, tractor, day, hour):
        res = send(client, tractor.opt_hour, day, hour, 2)
        assert res.status_code == 201, res.json()
        return res.json()["expires_at"]

    def test_tomorrow_two_hours(self, as_renter, tractor):
        assert self.expires(as_renter, tractor, TUE, 8) == "2026-11-23T09:30:00+05:30"

    def test_two_days_ahead_six_hours(self, as_renter, tractor):
        assert self.expires(as_renter, tractor, WED, 8) == "2026-11-23T13:30:00+05:30"

    def test_soon_ends_at_the_start(self, as_renter, tractor):
        assert self.expires(as_renter, tractor, MON, 8) == "2026-11-23T08:00:00+05:30"  # 07:30 + 2 h > 08:00


class TestRefused:
    def test_slot_taken(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        assert code(send(as_renter, tractor.opt_hour, TUE, 11, 2)) == "slot_taken"  # 11 is the gap
        assert send(as_renter, tractor.opt_hour, TUE, 12, 2).status_code == 201

    def test_another_option_on_the_same_machine_clashes(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_cult, renter, TUE, 8, 11)
        res = send(as_renter, tractor.opt_acre, TUE, 9, 1)
        assert res.status_code == 409 and code(res) == "slot_taken"

    def test_no_gap_back_to_back(self, as_renter, tractor, renter):
        tractor.gap_hours = 0
        tractor.save()
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, gap=0)
        assert send(as_renter, tractor.opt_hour, TUE, 11, 2).status_code == 201

    def test_outside_hours(self, as_renter, tractor):
        assert code(send(as_renter, tractor.opt_hour, TUE, 17, 2)) == "outside_hours"  # 17–19, closes 18
        assert code(send(as_renter, tractor.opt_hour, TUE, 5, 2)) == "outside_hours"

    def test_closed_day(self, as_renter, tractor):
        DayOverride.objects.create(listing=tractor, date=TUE, is_closed=True)
        assert code(send(as_renter, tractor.opt_hour, TUE, 8, 2)) == "outside_hours"

    def test_closed_weekday(self, as_renter, tractor):
        set_week(tractor, weekdays=range(6))  # रवि closed; 2026-11-29 is a Sunday
        assert code(send(as_renter, tractor.opt_hour, MON + timedelta(days=6), 8, 2)) == "outside_hours"

    def test_job_may_end_at_closing_time(self, as_renter, tractor):
        assert send(as_renter, tractor.opt_hour, TUE, 16, 2).status_code == 201

    def test_passed_hour(self, as_renter, tractor):
        res = send(as_renter, tractor.opt_hour, MON, 7, 2)  # it's 07:30
        assert res.status_code == 409 and code(res) == "slot_taken"

    def test_not_whole_hour(self, as_renter, tractor):
        b = body(tractor.opt_hour, TUE, 8, 2)
        b["start_at"] = (at(TUE, 8) + timedelta(minutes=30)).isoformat()
        assert code(as_renter.post(URL, b, format="json")) == "not_whole_hour"

    def test_below_min_hours(self, as_renter, tractor):
        assert code(send(as_renter, tractor.opt_hour, TUE, 8, 1)) == "below_min_hours"  # min 2

    def test_half_acre_steps_only(self, as_renter, tractor):
        assert code(send(as_renter, tractor.opt_acre, TUE, 8, "1.3")) == "validation_error"

    def test_more_than_30_days_ahead(self, as_renter, tractor):
        assert code(send(as_renter, tractor.opt_hour, MON + timedelta(days=30), 8, 2)) == "validation_error"

    def test_no_weekly_hours(self, as_renter, tractor):
        tractor.has_schedule = False
        tractor.save()
        assert code(send(as_renter, tractor.opt_hour, TUE, 8, 2)) == "not_bookable"

    def test_paused(self, as_renter, tractor):
        tractor.status = Listing.Status.PAUSED
        tractor.save()
        assert code(send(as_renter, tractor.opt_hour, TUE, 8, 2)) == "not_bookable"

    def test_removed_listing_or_old_price(self, as_renter, tractor):
        tractor.opt_cult.is_active = False
        tractor.opt_cult.save()
        assert send(as_renter, tractor.opt_cult, TUE, 8, 2).status_code == 400
        tractor.status = Listing.Status.REMOVED
        tractor.save()
        assert send(as_renter, tractor.opt_hour, TUE, 8, 2).status_code == 400

    def test_own_machine(self, as_owner, tractor):
        assert code(send(as_owner, tractor.opt_hour, TUE, 8, 2)) == "own_listing"

    def test_logged_out(self, api, tractor):
        assert send(api, tractor.opt_hour, TUE, 8, 2).status_code == 401


class TestStaleRequests:
    def test_an_expired_request_frees_the_time_and_is_marked(self, as_renter, tractor, renter):
        old = book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED,
                   expires_at=at(MON, 7))  # the owner never answered
        res = send(as_renter, tractor.opt_hour, TUE, 8, 3)
        assert res.status_code == 201, res.json()
        old.refresh_from_db()
        assert old.status == Booking.Status.EXPIRED
        assert BookingEvent.objects.filter(booking=old, to_status="expired").exists()

    def test_the_chips_and_the_check_agree(self, as_renter, tractor, renter):
        """Every chip the app shows can be sent; every other hour is refused (one rule, 05 §4)."""
        book(tractor, tractor.opt_hour, renter, TUE, 9, 11)
        chips = starts(as_renter, tractor, tractor.opt_hour, TUE, 2).json()["start_hours"]
        now = at(MON, 7) + timedelta(minutes=30)
        for hour in range(0, 24):
            if hour in chips:
                assert booking_requests.check(tractor.opt_hour, at(TUE, hour), Decimal(2), now).slots
            else:
                with pytest.raises(ApiError):
                    booking_requests.check(tractor.opt_hour, at(TUE, hour), Decimal(2), now)


class TestDatabaseIsTheLastGuard:
    def test_overlap_that_slips_past_the_code_is_still_409(self, as_renter, tractor, renter, monkeypatch):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        monkeypatch.setattr(Plan, "clashes", lambda *a, **k: False)  # pretend the code has a bug
        res = send(as_renter, tractor.opt_hour, TUE, 9, 2)
        assert res.status_code == 409 and code(res) == "slot_taken"
        assert Booking.objects.filter(listing=tractor).count() == 1


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
class TestTwoAtTheSameMoment:
    """The roadmap's test: 2 parallel overlapping requests → exactly one 201, one 409, one active row."""

    def test_exactly_one_wins(self, tractor, renter, django_user_model):
        other = django_user_model.objects.create_user(phone="+919800000001", firebase_uid="uid-2", name="Mohan")
        barrier = threading.Barrier(2)
        results = []

        def go(user, hour):
            client = APIClient()
            client.force_authenticate(user)
            try:
                barrier.wait()
                results.append(send(client, tractor.opt_hour, TUE, hour, 3).status_code)
            finally:
                connection.close()

        threads = [threading.Thread(target=go, args=(renter, 8)), threading.Thread(target=go, args=(other, 9))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results) == [201, 409]
        assert Booking.objects.filter(listing=tractor, status="requested").count() == 1


class TestQuote:
    def test_estimate_without_holding(self, as_renter, tractor):
        res = as_renter.post(URL + "quote/", {"option_id": tractor.opt_acre.pk, "start_at": at(TUE, 8).isoformat(),
                                              "quantity": "2.5"}, format="json")
        assert res.status_code == 200, res.json()
        assert res.json() == {
            "available": True, "hours": 4, "start_at": "2026-11-24T08:00:00+05:30",
            "end_at": "2026-11-24T12:00:00+05:30", "blocked_until": "2026-11-24T13:00:00+05:30",
            "days": None, "estimated_total": "3750.00", "price_unit": "acre",
        }
        assert not Booking.objects.exists()

    def test_taken(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = as_renter.post(URL + "quote/", {"option_id": tractor.opt_hour.pk, "start_at": at(TUE, 9).isoformat(),
                                              "quantity": "2"}, format="json")
        assert res.status_code == 409 and code(res) == "slot_taken"


class TestReadOne:
    def make(self, client, tractor):
        res = send(client, tractor.opt_hour, TUE, 8, 2)
        assert res.status_code == 201
        return res.json()["id"]

    def test_renter_and_owner_see_it_without_contact_details(self, as_renter, tractor, owner):
        pk = self.make(as_renter, tractor)
        j = as_renter.get(f"{URL}{pk}/").json()
        assert j["owner"] == {"name": "Ramesh"} and j["renter"] == {"name": "Suresh", "village": "सोरांव"}  # S17
        assert "contact" not in j
        text = str(j)
        for secret in ("+91", "पुल के पास", "मंदिर के पास", "25.51"):
            assert secret not in text  # no phone, no landmark, no pin before acceptance (05 §7)
        owner_view = APIClient()
        owner_view.force_authenticate(owner)
        assert owner_view.get(f"{URL}{pk}/").status_code == 200

    def test_contact_only_after_acceptance(self, as_renter, tractor):
        pk = self.make(as_renter, tractor)
        Booking.objects.filter(pk=pk).update(status=Booking.Status.ACCEPTED)
        j = as_renter.get(f"{URL}{pk}/").json()
        assert j["contact"]["owner_phone"] == "+919876543210"
        assert j["contact"]["field"]["landmark"] == "पुल के पास"

    def test_anyone_else_gets_404(self, as_renter, tractor, django_user_model):
        pk = self.make(as_renter, tractor)
        stranger = APIClient()
        stranger.force_authenticate(django_user_model.objects.create_user(phone="+919800000009", name="X"))
        assert stranger.get(f"{URL}{pk}/").status_code == 404


def test_job_codes_exist(db):
    assert JobCategory.objects.filter(code__in=["land_prep", "other"]).count() == 2
