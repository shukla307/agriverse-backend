"""GET /api/search/?date=…: "कब चाहिए?" (decision §21). A machine shows only if at least one hour is still free
that day, by the same rules as the start-time chips (open hours, other bookings + gaps, not in the past)."""

from datetime import timedelta

from bookings.availability import at
from bookings.models import Booking
from listings.models import DayOverride, Listing, ListingOption

from .conftest import MON, TUE, WED, book, set_week

HOME = {"lat": 25.50, "lng": 81.85, "radius_km": 25}  # the renter's village; the tractor is ~8.6 km away


def search(client, **params):
    res = client.get("/api/search/", {**HOME, **params})
    assert res.status_code == 200, res.json()
    body = res.json()
    return [r["id"] for r in body["results"]], body["hint"]


def test_free_tomorrow_is_found(as_renter, tractor):
    assert search(as_renter, date=TUE.isoformat()) == ([tractor.pk], None)


def test_without_a_date_nothing_changes(as_renter, tractor):
    DayOverride.objects.create(listing=tractor, date=TUE, is_closed=True)
    assert search(as_renter)[0] == [tractor.pk]


def test_closed_that_day(as_renter, tractor):
    DayOverride.objects.create(listing=tractor, date=TUE, is_closed=True)
    assert search(as_renter, date=TUE.isoformat()) == ([], "no_match_date")
    assert search(as_renter, date=WED.isoformat())[0] == [tractor.pk]


def test_closed_weekday(as_renter, tractor):
    set_week(tractor, weekdays=[0, 2, 3, 4, 5, 6])  # मंगल closed every week
    assert search(as_renter, date=TUE.isoformat()) == ([], "no_match_date")


def test_changed_hours_on_a_closed_weekday_open_it(as_renter, tractor):
    set_week(tractor, weekdays=[0, 2, 3, 4, 5, 6])
    DayOverride.objects.create(listing=tractor, date=TUE, start_hour=8, end_hour=12)
    assert search(as_renter, date=TUE.isoformat())[0] == [tractor.pk]


def test_fully_booked_that_day(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 6, 18)
    assert search(as_renter, date=TUE.isoformat()) == ([], "no_match_date")


def test_partly_booked_still_shows(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
    assert search(as_renter, date=TUE.isoformat())[0] == [tractor.pk]


def test_the_gap_counts(as_renter, tractor, renter):
    # 6–17 + 1 h gap reserves until 18 = the whole day.
    book(tractor, tractor.opt_hour, renter, TUE, 6, 17)
    assert search(as_renter, date=TUE.isoformat())[0] == []


def test_an_expired_request_doesnt_hide_it(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 6, 18, status=Booking.Status.REQUESTED, expires_at=at(MON, 7))
    assert search(as_renter, date=TUE.isoformat())[0] == [tractor.pk]


def test_today_only_while_an_hour_is_left(as_renter, tractor, frozen):
    assert search(as_renter, date=MON.isoformat())[0] == [tractor.pk]  # 07:30, open until 18
    frozen.move_to("2026-11-23 11:20:00")  # 16:50 IST: a start at 17 is still ahead
    assert search(as_renter, date=MON.isoformat())[0] == [tractor.pk]
    frozen.move_to("2026-11-23 11:45:00")  # 17:15 IST: the next start would be 18 = closing, like the chips
    assert search(as_renter, date=MON.isoformat()) == ([], "no_match_date")


def test_no_hours_set_hidden_with_a_date(as_renter, tractor):
    tractor.has_schedule = False
    tractor.save()
    assert search(as_renter)[0] == [tractor.pk]  # a plain search still shows it ("मालिक ने अभी समय नहीं बताया")
    ids, hint = search(as_renter, date=TUE.isoformat())
    assert ids == [] and hint == "no_match_date"


def test_paused_never(as_renter, tractor):
    tractor.status = Listing.Status.PAUSED
    tractor.save()
    assert search(as_renter, date=TUE.isoformat()) == ([], "no_match")


def test_only_the_busy_machine_is_hidden(as_renter, tractor, renter, owner):
    other = Listing.objects.create(owner=owner, machine_type=tractor.machine_type, lat="25.45", lng="81.90",
                                   gap_hours=0)
    ListingOption.objects.create(listing=other, price_unit="hour", price=500)
    set_week(other)
    book(tractor, tractor.opt_hour, renter, TUE, 6, 18)
    assert search(as_renter, date=TUE.isoformat())[0] == [other.pk]


def test_free_that_day_but_farther_away(as_renter, tractor):
    # Searching 5 km around a point 30 km off: the tractor is free that day, just not near → a bigger circle.
    ids, hint = search(as_renter, date=TUE.isoformat(), lat=25.70, lng=81.70, radius_km=5)
    assert ids == [] and hint == "none_in_radius"


def test_far_away_and_busy_that_day(as_renter, tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 6, 18)
    ids, hint = search(as_renter, date=TUE.isoformat(), lat=25.70, lng=81.70, radius_km=5)
    assert ids == [] and hint == "no_match_date"  # a bigger circle wouldn't help; another day would


def test_job_chip_is_named_before_the_date(as_renter, tractor):
    # "drone" with जुताई on and a date: the job chip hides the drone first (BUGS.md #11 order).
    ids, hint = search(as_renter, date=TUE.isoformat(), q="drone", job="land_prep")
    assert ids == [] and hint in ("no_match", "no_match_job")


def test_date_outside_the_30_days(as_renter, tractor):
    for d in (MON - timedelta(days=1), MON + timedelta(days=30)):
        res = as_renter.get("/api/search/", {**HOME, "date": d.isoformat()})
        assert res.status_code == 400 and res.json()["error"]["fields"]["date"] == ["outside_30_days"]


def test_queries_dont_grow_with_listings(as_renter, tractor, renter, owner, django_assert_max_num_queries):
    for i in range(15):
        x = Listing.objects.create(owner=owner, machine_type=tractor.machine_type, lat="25.45", lng="81.90")
        ListingOption.objects.create(listing=x, price_unit="hour", price=500)
        set_week(x)
        book(x, ListingOption.objects.get(listing=x), renter, TUE, 8, 10)
    with django_assert_max_num_queries(14):
        assert len(search(as_renter, date=TUE.isoformat())[0]) == 16
