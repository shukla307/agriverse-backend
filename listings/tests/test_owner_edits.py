"""My listings, edit, pause, remove, and 'someone else's listing is 404' (06 §1, §5)."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from listings.models import Listing

from .conftest import photo, tractor_payload

pytestmark = pytest.mark.django_db


def test_mine_lists_my_active_and_paused_newest_first(me, stranger, api):
    first = me.post("/api/listings/", tractor_payload(), format="json").json()["id"]
    second = me.post("/api/listings/", tractor_payload(photos=[photo()]), format="json").json()["id"]
    me.patch(f"/api/listings/{first}/", {"status": "paused"}, format="json")
    third = me.post("/api/listings/", tractor_payload(), format="json").json()["id"]
    me.delete(f"/api/listings/{third}/")

    body = me.get("/api/listings/mine/").json()
    assert body["count"] == 2 and body["next_page"] is None
    assert [r["id"] for r in body["results"]] == [second, first]
    card = body["results"][0]
    assert card["cover_thumb_url"].endswith("a_thumb.jpg?alt=media&token=t")
    assert card["options_count"] == 3
    assert card["next_booking"] is None and card["pending_requests_count"] == 0
    assert body["results"][1]["status"] == "paused"
    assert body["results"][1]["cover_thumb_url"] is None  # the app shows the machine's icon

    api.force_authenticate(stranger)
    assert api.get("/api/listings/mine/").json()["count"] == 0


def test_mine_is_paginated_20_per_page(me, owner):
    base = Listing.objects.get(pk=me.post("/api/listings/", tractor_payload(), format="json").json()["id"])
    for _ in range(20):
        base.pk = None
        base.save()
    page1 = me.get("/api/listings/mine/").json()
    assert page1["count"] == 21 and len(page1["results"]) == 20 and page1["next_page"] == 2
    assert len(me.get("/api/listings/mine/?page=2").json()["results"]) == 1


def test_mine_query_count_does_not_grow(me):
    for _ in range(3):
        me.post("/api/listings/", tractor_payload(photos=[photo()]), format="json")
    with CaptureQueriesContext(connection) as small:
        me.get("/api/listings/mine/")
    for _ in range(5):
        me.post("/api/listings/", tractor_payload(photos=[photo()]), format="json")
    with CaptureQueriesContext(connection) as big:
        me.get("/api/listings/mine/")
    assert len(big.captured_queries) == len(small.captured_queries)


@pytest.mark.parametrize("method", ["patch", "delete"])
def test_someone_elses_listing_is_404(api, stranger, listing_id, method):
    """Changing someone else's listing: 404. (Reading it is the renter view since Phase 4: test_details.py.)"""
    api.force_authenticate(stranger)
    res = getattr(api, method)(f"/api/listings/{listing_id}/", {"status": "paused"}, format="json")
    assert res.status_code == 404
    assert res.json()["error"]["code"] == "not_found"
    assert Listing.objects.get(pk=listing_id).status == "active"


def test_someone_elses_listing_parts_are_404(api, me, owner, stranger, listing_id):
    """Valid bodies, so the ONLY reason to refuse is that the listing isn't theirs."""
    pid = me.post(f"/api/listings/{listing_id}/photos/", photo(), format="json").json()["id"]
    oid = me.get(f"/api/listings/{listing_id}/").json()["options"][1]["id"]
    api.force_authenticate(stranger)
    base = f"/api/listings/{listing_id}"
    calls = [
        api.post(f"{base}/options/", {"price_unit": "hour", "price": "1"}, format="json"),
        api.put(f"{base}/options/", {"options": [{"price_unit": "hour", "price": "1"}]}, format="json"),
        api.patch(f"{base}/options/{oid}/", {"price": "1"}, format="json"),
        api.delete(f"{base}/options/{oid}/"),
        api.post(f"{base}/photos/", photo(uid="uid-stranger"), format="json"),
        api.patch(f"{base}/photos/order/", {"photo_ids": [pid]}, format="json"),
        api.delete(f"{base}/photos/{pid}/"),
        api.put(f"{base}/weekly-hours/", {"days": [{"weekday": 0, "start_hour": 6, "end_hour": 7}]}, format="json"),
    ]
    assert [c.status_code for c in calls] == [404] * len(calls)
    api.force_authenticate(owner)  # (me and api are the same test client)
    me_view = me.get(f"/api/listings/{listing_id}/").json()
    assert len(me_view["options"]) == 3 and len(me_view["photos"]) == 1
    assert me_view["weekly_hours"][0] == {"weekday": 0, "start_hour": 7, "end_hour": 19}  # unchanged


def test_owner_gets_the_full_listing(me, listing_id):
    body = me.get(f"/api/listings/{listing_id}/").json()
    assert body["id"] == listing_id and len(body["options"]) == 3


def test_patch_fields_and_pause_resume(me, listing_id):
    res = me.patch(f"/api/listings/{listing_id}/", {"brand": " Swaraj ", "hp": 50, "gap_hours": 2,
                                                   "transport_included": False, "status": "paused"}, format="json")
    body = res.json()
    assert res.status_code == 200
    # Gap and transport are fixed (decision §22): an old app sending them changes nothing.
    assert (body["brand"], body["hp"], body["gap_hours"], body["transport_included"]) == ("Swaraj", 50, 1, True)
    assert body["status"] == "paused" and body["bookable"] is False
    assert me.patch(f"/api/listings/{listing_id}/", {"status": "active"}, format="json").json()["status"] == "active"


def test_patch_cannot_remove_or_change_machine(me, listing_id):
    assert me.patch(f"/api/listings/{listing_id}/", {"status": "removed"}, format="json").status_code == 400
    me.patch(f"/api/listings/{listing_id}/", {"machine_type_id": 1, "operator_included": False}, format="json")
    body = me.get(f"/api/listings/{listing_id}/").json()
    assert body["machine"]["code"] == "tractor" and body["operator_included"] is True


def test_patch_moves_the_machine_to_a_map_pin(me, listing_id):
    res = me.patch(f"/api/listings/{listing_id}/", {"location": {"lat": 25.5, "lng": 81.8, "village": "फाफामऊ"}},
                   format="json")
    assert res.json()["location"]["village"] == "फाफामऊ"
    assert res.json()["location"]["landmark"] == ""


def test_removed_listing_is_gone_for_the_owner_too(me, listing_id):
    assert me.delete(f"/api/listings/{listing_id}/").status_code == 204
    assert Listing.objects.get(pk=listing_id).status == "removed"  # kept for old bookings
    assert me.get(f"/api/listings/{listing_id}/").status_code == 404
    assert me.patch(f"/api/listings/{listing_id}/", {"status": "active"}, format="json").status_code == 404
