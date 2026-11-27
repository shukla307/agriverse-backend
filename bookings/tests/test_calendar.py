"""GET /api/listings/{id}/calendar/: the 30-day strip (S10, S11) and the owner's calendar (S15 step 2)."""

from datetime import timedelta

from bookings.models import Booking
from listings.models import DayOverride

from .conftest import MON, TUE, WED, book


def cal(client, listing, **params):
    res = client.get(f"/api/listings/{listing.pk}/calendar/", params)
    assert res.status_code == 200, res.json()
    return res.json()


def day(body, d):
    return next(x for x in body["days"] if x["date"] == d.isoformat())


def test_thirty_days_from_today(as_renter, tractor):
    body = cal(as_renter, tractor)
    assert len(body["days"]) == 30
    assert body["days"][0]["date"] == MON.isoformat()
    assert body["timezone"] == "Asia/Kolkata" and body["gap_hours"] == 1 and body["bookable"] is True
    assert len(body["weekly_hours"]) == 7


def test_free_booked_closed(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
    DayOverride.objects.create(listing=tractor, date=WED, is_closed=True)
    body = cal(as_renter, tractor)
    assert day(body, MON)["status"] == "free"
    assert day(body, MON)["open"] == {"start_hour": 6, "end_hour": 18}
    assert day(body, TUE)["status"] == "booked"
    assert day(body, TUE)["busy"] == [{"start_hour": 8, "end_hour": 12}]  # work + gap
    assert day(body, WED) == {"date": WED.isoformat(), "status": "closed", "open": None, "busy": []}


def test_renters_see_hours_only(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 10)
    book(tractor, tractor.opt_cult, renter, TUE, 11, 13)  # 8–11 and 11–14 touch → one span
    busy = day(cal(as_renter, tractor), TUE)["busy"]
    assert busy == [{"start_hour": 8, "end_hour": 14}]
    assert "override" not in day(cal(as_renter, tractor), TUE)


def test_owner_sees_which_booking(as_owner, tractor, renter):
    b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED,
             expires_at=tractor.created_at + timedelta(days=1))
    DayOverride.objects.create(listing=tractor, date=WED, start_hour=8, end_hour=12)
    body = cal(as_owner, tractor)
    assert day(body, TUE)["busy"] == [{
        "start_hour": 8, "end_hour": 12, "booking_id": b.pk, "status": "requested",
        "renter_name": "Suresh", "work": {"start_hour": 8, "end_hour": 11},  # S15's day sheet: who + work only
    }]
    assert day(body, WED)["override"] is True and day(body, TUE)["override"] is False


def test_gap_spilling_past_midnight_shows_on_the_next_day(as_renter, tractor, renter):
    from .conftest import set_week

    set_week(tractor, start=0, end=24)
    book(tractor, tractor.opt_hour, renter, TUE, 22, 24)
    body = cal(as_renter, tractor)
    assert day(body, TUE)["busy"] == [{"start_hour": 22, "end_hour": 24}]
    assert day(body, WED)["busy"] == [{"start_hour": 0, "end_hour": 1}]
    assert day(body, WED)["status"] == "booked"


def test_owner_rest_hour_after_midnight_has_no_work(as_owner, tractor, renter):
    from .conftest import set_week

    set_week(tractor, start=0, end=24)
    book(tractor, tractor.opt_hour, renter, TUE, 22, 24)
    body = cal(as_owner, tractor)
    assert day(body, TUE)["busy"][0]["work"] == {"start_hour": 22, "end_hour": 24}
    assert day(body, WED)["busy"][0]["work"] is None  # only the rest hour: S15 doesn't list it as a booking


def test_a_booking_only_in_the_gap_after_closing_is_not_booked(as_renter, tractor, renter):
    # 16–18 reserves until 19; 18–19 is after closing, so the next day is untouched and this day is booked once.
    book(tractor, tractor.opt_hour, renter, TUE, 16, 18)
    assert day(cal(as_renter, tractor), WED)["status"] == "free"


def test_no_schedule_every_day_closed(as_renter, tractor):
    tractor.has_schedule = False
    tractor.save()
    body = cal(as_renter, tractor)
    assert body["has_schedule"] is False and body["bookable"] is False
    assert {d["status"] for d in body["days"]} == {"closed"}


def test_paused_closed_for_renters_but_not_for_the_owner(as_renter, as_owner, tractor, api, renter):
    tractor.status = "paused"
    tractor.save()
    api.force_authenticate(renter)
    assert {d["status"] for d in cal(api, tractor)["days"]} == {"closed"}
    api.force_authenticate(tractor.owner)
    assert cal(api, tractor)["days"][0]["status"] == "free"  # the owner plans ahead while paused


def test_from_and_days(as_renter, tractor):
    body = cal(as_renter, tractor, **{"from": TUE.isoformat(), "days": 3})
    assert [d["date"] for d in body["days"]] == [TUE.isoformat(), WED.isoformat(), (WED + timedelta(days=1)).isoformat()]


def test_bad_params(as_renter, tractor):
    url = f"/api/listings/{tractor.pk}/calendar/"
    assert as_renter.get(url, {"days": 31}).status_code == 400
    assert as_renter.get(url, {"from": "nonsense"}).status_code == 400
    assert as_renter.get(url, {"from": (MON - timedelta(days=1)).isoformat()}).status_code == 400


def test_three_queries_whatever_the_bookings(as_renter, tractor, renter, django_assert_max_num_queries):
    for i in range(10):
        book(tractor, tractor.opt_hour, renter, MON + timedelta(days=i + 1), 8, 10)
    with django_assert_max_num_queries(7):  # auth user + listing + weekly + overrides + bookings
        cal(as_renter, tractor)


def test_today_after_closing_time_is_closed(as_renter, tractor, frozen):
    """BUGS.md #23 (emulator, 2026-11-27): at 9 PM "आज" was green खाली, but no start time was left."""
    frozen.move_to("2026-11-23 12:30:00")  # 18:30 IST: this fixture closes at 18, so nothing can start today
    body = cal(as_renter, tractor)
    assert day(body, MON)["status"] == "closed"
    assert day(body, TUE)["status"] == "free"
    frozen.move_to("2026-11-23 11:00:00")  # 16:30 IST: 17 is still a start hour
    assert day(cal(as_renter, tractor), MON)["status"] == "free"
