"""GET /api/listings/{id}/: the owner's view vs the renter's view (06 §7, Phase 4 slice 3)."""

import json
import math

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from accounts.models import User
from listings.models import Listing, ListingOption

from .conftest import photo


@pytest.fixture
def full_listing(me, listing_id, owner):
    """The conftest tractor + 2 photos + weekly hours, owned by "Ramesh Kumar" who lives in झूंसी."""
    me.post(f"/api/listings/{listing_id}/photos/", photo(name="a"), format="json")
    me.post(f"/api/listings/{listing_id}/photos/", photo(name="b"), format="json")
    week = [{"weekday": d, "start_hour": 6, "end_hour": 18} for d in range(6)]
    assert me.put(f"/api/listings/{listing_id}/weekly-hours/", {"days": week}, format="json").status_code == 200
    owner.name = "Ramesh Kumar"
    owner.save()
    return Listing.objects.get(pk=listing_id)


@pytest.fixture
def renter(stranger):
    """Its OWN client: `me` and `api` are the same object, so logging that one in as someone else would
    also change who `me` is (LEARNING, Phase 3 slice 1)."""
    client = APIClient()
    client.force_authenticate(stranger)
    return client


def details(client, listing_id, **params):
    res = client.get(f"/api/listings/{listing_id}/", params)
    assert res.status_code == 200, res.json()
    return res.json()


def test_needs_login(listing_id):
    assert APIClient().get(f"/api/listings/{listing_id}/").status_code == 401


def test_owner_still_gets_the_owner_view(me, full_listing):
    body = details(me, full_listing.id)
    assert body["location"]["landmark"] == "मंदिर के पास"  # copied from the owner's home address
    assert "storage_path" in body["photos"][0]


def test_renter_view(renter, full_listing):
    body = details(renter, full_listing.id)
    assert body["machine"]["code"] == "tractor" and body["brand"] == "Mahindra" and body["hp"] == 45
    assert body["year"] == 2019 and body["status"] == "active" and body["bookable"] is True
    assert body["operator_included"] is True and body["transport_included"] is True
    assert body["diesel_included"] is False and body["gap_hours"] == 1
    assert body["village"] == "झूंसी"
    assert [p["url"].split("%2F")[-1].split("?")[0] for p in body["photos"]] == ["a_full.jpg", "b_full.jpg"]
    assert set(body["photos"][0]) == {"url", "thumb_url"}
    assert body["weekly_hours"][0] == {"weekday": 0, "start_hour": 6, "end_hour": 18}
    assert len(body["weekly_hours"]) == 6
    assert body["owner"] == {"first_name": "Ramesh", "village": "झूंसी",
                             "member_since": full_listing.owner.date_joined.strftime("%Y-%m")}
    assert body["distance_km"] is None  # no point sent


def test_prices_active_only_with_hours_per_acre_for_acre(renter, me, full_listing):
    removed = full_listing.options.get(implement__code="cultivator")
    assert me.delete(f"/api/listings/{full_listing.id}/options/{removed.id}/").status_code == 204
    options = details(renter, full_listing.id)["options"]
    assert len(options) == 3 and removed.id not in [o["id"] for o in options]
    acre = next(o for o in options if o["price_unit"] == "acre")
    assert acre["implement"]["code"] == "rotavator" and acre["price"] == "1500.00"
    assert acre["hours_per_acre"] == "1.5"  # rotavator's default (05 §3.4a), owner left it empty
    hour = next(o for o in options if o["price_unit"] == "hour" and o["implement"] is None)
    assert hour["min_hours"] == 2 and hour["hours_per_acre"] is None


def test_privacy(renter, full_listing, owner):
    body = details(renter, full_listing.id, lat=25.4447, lng=81.8282)
    text = json.dumps(body, ensure_ascii=False)
    for secret in (owner.phone, owner.phone[3:], "मंदिर के पास", "Kumar", str(full_listing.lat), str(full_listing.lng),
                   "उत्तर प्रदेश"):
        assert secret not in text, secret
    for key in ("location", "landmark", "lat", "lng", "phone", "owner_id", "storage_path", "district", "state",
                "bookings_count", "has_schedule"):
        assert key not in body, key
    assert set(body["owner"]) == {"first_name", "village", "member_since"}


def test_distance_from_the_renters_point(renter, full_listing):
    junction = (25.4447, 81.8282)
    body = details(renter, full_listing.id, lat=junction[0], lng=junction[1])
    a, b = map(math.radians, junction), map(math.radians, (float(full_listing.lat), float(full_listing.lng)))
    (lat1, lng1), (lat2, lng2) = a, b
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    assert body["distance_km"] == round(2 * 6371 * math.asin(math.sqrt(h)), 1)
    # Same number as the search card shows for the same point.
    card = renter.get("/api/search/", {"lat": junction[0], "lng": junction[1], "radius_km": 25}).json()["results"][0]
    assert card["id"] == full_listing.id and card["distance_km"] == body["distance_km"]


@pytest.mark.parametrize("params", [{"lat": 25.4}, {"lng": 81.8}, {"lat": "x", "lng": 81.8}, {"lat": 95, "lng": 81}])
def test_half_a_point_is_a_400(renter, full_listing, params):
    res = renter.get(f"/api/listings/{full_listing.id}/", params)
    assert res.status_code == 400 and res.json()["error"]["code"] == "validation_error"


def test_paused_opens_with_its_status(renter, me, full_listing):
    me.patch(f"/api/listings/{full_listing.id}/", {"status": "paused"}, format="json")
    body = details(renter, full_listing.id)
    assert body["status"] == "paused" and body["bookable"] is False


def test_not_bookable_without_hours(renter, listing_id):
    """Owner chose "बाद में" in the wizard: no hours yet. (An empty week can't be PUT: at least one day.)"""
    body = details(renter, listing_id)
    assert body["bookable"] is False and body["weekly_hours"] == []


@pytest.mark.parametrize("how", ["removed", "owner_disabled", "missing"])
def test_gone_is_404(renter, me, full_listing, how):
    listing_id = full_listing.id
    if how == "removed":
        me.delete(f"/api/listings/{listing_id}/")
    elif how == "owner_disabled":
        User.objects.filter(pk=full_listing.owner_id).update(is_active=False)
    else:
        listing_id += 999
    res = renter.get(f"/api/listings/{listing_id}/")
    assert res.status_code == 404 and res.json()["error"]["code"] == "not_found"


def test_query_count_does_not_grow_with_prices_or_photos(renter, me, full_listing):
    def count():
        with CaptureQueriesContext(connection) as ctx:
            details(renter, full_listing.id, lat=25.44, lng=81.83)
        return len(ctx)

    few = count()
    for name in ("c", "d", "e"):
        me.post(f"/api/listings/{full_listing.id}/photos/", photo(name=name), format="json")
    for unit in ("day", "negotiable"):
        ListingOption.objects.create(listing=full_listing, price_unit=unit, price=None if unit == "negotiable" else 5000)
    assert count() == few
