"""Options, photos and weekly hours of one listing (06 §5, §6)."""

import pytest

from listings.models import ListingOption

from .conftest import mt, photo, tractor_payload

pytestmark = pytest.mark.django_db


def opts(me, lid):
    return me.get(f"/api/listings/{lid}/").json()["options"]


# ---------------------------------------------------------------- options

def test_add_option(me, listing_id):
    res = me.post(f"/api/listings/{listing_id}/options/",
                  {"implement_id": mt("trolley").id, "price_unit": "hour", "price": "1200"}, format="json")
    assert res.status_code == 201
    assert res.json()["implement"]["code"] == "trolley"
    assert len(opts(me, listing_id)) == 4


def test_add_option_in_another_unit_refused(me, listing_id):
    """Decision §22: one unit per machine. Switching units goes through PUT options/ (the whole list)."""
    res = me.post(f"/api/listings/{listing_id}/options/",
                  {"implement_id": mt("trolley").id, "price_unit": "day", "price": "1200"}, format="json")
    assert res.status_code == 400 and res.json()["error"]["fields"]["price_unit"] == "one_unit"


def test_add_duplicate_option_refused(me, listing_id):
    res = me.post(f"/api/listings/{listing_id}/options/", {"price_unit": "hour", "price": "650"}, format="json")
    assert res.status_code == 400
    assert res.json()["error"]["fields"]["options"] == "duplicate_option"


def test_edit_price_only(me, listing_id):
    oid = opts(me, listing_id)[0]["id"]
    res = me.patch(f"/api/listings/{listing_id}/options/{oid}/", {"price": "700"}, format="json")
    assert res.status_code == 200
    assert res.json()["price"] == "700.00" and res.json()["min_hours"] == 1


def test_change_unit_of_one_price_refused_while_others_use_the_old_unit(me, listing_id):
    oid = opts(me, listing_id)[0]["id"]
    res = me.patch(f"/api/listings/{listing_id}/options/{oid}/", {"price_unit": "day", "price": "4000"},
                   format="json")
    assert res.status_code == 400 and res.json()["error"]["fields"]["price_unit"] == "one_unit"


def test_change_unit_to_acre_needs_hours_when_there_is_no_default(me):
    lid = me.post("/api/listings/", tractor_payload(options=[{"price_unit": "hour", "price": "600"}]),
                  format="json").json()["id"]
    oid = opts(me, lid)[0]["id"]  # the only price: its unit may change; tractor alone has no default
    res = me.patch(f"/api/listings/{lid}/options/{oid}/", {"price_unit": "acre", "price": "1200"}, format="json")
    assert res.json()["error"]["fields"]["hours_per_acre"] == ["required"]


# ---------------------------------------------------------------- the whole price list (S21 Edit)

def put_options(me, lid, options):
    return me.put(f"/api/listings/{lid}/options/", {"options": options}, format="json")


def test_put_options_changes_prices_in_place_and_keeps_ids(me, listing_id):
    before = {(o["implement"] or {}).get("code"): o["id"] for o in opts(me, listing_id)}
    res = put_options(me, listing_id, [
        {"price_unit": "hour", "price": "650"},
        {"implement_id": mt("rotavator").id, "price_unit": "hour", "price": "900"},
        {"implement_id": mt("harrow").id, "price_unit": "hour", "price": "700"},
    ])  # cultivator dropped, harrow added
    assert res.status_code == 200, res.json()
    after = {(o["implement"] or {}).get("code"): o for o in res.json()["options"]}
    assert set(after) == {None, "rotavator", "harrow"}
    assert after[None]["id"] == before[None] and after[None]["price"] == "650.00"
    assert after["rotavator"]["id"] == before["rotavator"]
    assert ListingOption.objects.filter(pk=before["cultivator"], is_active=False).exists()  # kept for bookings


def test_put_options_switches_the_unit_in_one_go(me, listing_id):
    res = put_options(me, listing_id, [
        {"price_unit": "acre", "price": "1200", "hours_per_acre": "1.5"},
        {"implement_id": mt("rotavator").id, "price_unit": "acre", "price": "1500"},
    ])
    assert res.status_code == 200, res.json()
    assert {o["price_unit"] for o in res.json()["options"]} == {"acre"}
    assert len(res.json()["options"]) == 2


def test_put_options_refuses_two_units_and_changes_nothing(me, listing_id):
    before = opts(me, listing_id)
    res = put_options(me, listing_id, [
        {"price_unit": "hour", "price": "650"},
        {"implement_id": mt("rotavator").id, "price_unit": "acre", "price": "1500"},
    ])
    assert res.status_code == 400 and res.json()["error"]["fields"]["options"] == ["one_unit"]
    assert opts(me, listing_id) == before


def test_put_options_needs_at_least_one(me, listing_id):
    assert put_options(me, listing_id, []).status_code == 400


def test_remove_option_but_never_the_last(me):
    lid = me.post("/api/listings/", tractor_payload(options=[
        {"price_unit": "hour", "price": "600"},
        {"implement_id": mt("rotavator").id, "price_unit": "hour", "price": "900"}]), format="json").json()["id"]
    first, second = [o["id"] for o in opts(me, lid)]
    assert me.delete(f"/api/listings/{lid}/options/{first}/").status_code == 204
    res = me.delete(f"/api/listings/{lid}/options/{second}/")
    assert res.status_code == 400
    assert res.json()["error"]["fields"]["options"] == "at_least_one"
    # The removed one is kept (inactive) for old bookings, and can be added again.
    assert ListingOption.objects.filter(pk=first, is_active=False).exists()
    assert me.post(f"/api/listings/{lid}/options/", {"price_unit": "hour", "price": "650"},
                   format="json").status_code == 201


# ---------------------------------------------------------------- photos

def test_add_photos_up_to_five(me, listing_id):
    for i in range(5):
        res = me.post(f"/api/listings/{listing_id}/photos/", photo(name=str(i)), format="json")
        assert res.status_code == 201 and res.json()["sort_order"] == i
    res = me.post(f"/api/listings/{listing_id}/photos/", photo(name="6"), format="json")
    assert res.status_code == 400 and res.json()["error"]["code"] == "too_many_photos"


@pytest.mark.parametrize("bad", [
    lambda p: {**p, "storage_path": p["storage_path"].replace("uid-owner", "uid-stranger")},
    lambda p: {**p, "url": p["url"].replace("agriverse-main", "evil-bucket")},
    lambda p: {**p, "url": "https://example.com/a_full.jpg"},
    lambda p: {**p, "thumb_url": p["url"]},
    lambda p: {**p, "storage_path": "listings/uid-owner/../uid-x/a_full.jpg"},
    lambda p: {**p, "storage_path": "profiles/uid-owner/avatar.jpg"},
])
def test_photo_must_be_mine_in_our_bucket_as_a_full_thumb_pair(me, listing_id, bad):
    res = me.post(f"/api/listings/{listing_id}/photos/", bad(photo()), format="json")
    assert res.status_code == 400


def test_reorder_photos_makes_a_new_cover(me, listing_id):
    ids = [me.post(f"/api/listings/{listing_id}/photos/", photo(name=n), format="json").json()["id"] for n in "abc"]
    res = me.patch(f"/api/listings/{listing_id}/photos/order/", {"photo_ids": [ids[2], ids[0], ids[1]]},
                   format="json")
    assert [p["id"] for p in res.json()] == [ids[2], ids[0], ids[1]]
    card = me.get("/api/listings/mine/").json()["results"][0]
    assert "c_thumb.jpg" in card["cover_thumb_url"]
    # Every photo exactly once:
    assert me.patch(f"/api/listings/{listing_id}/photos/order/", {"photo_ids": ids[:2]},
                    format="json").status_code == 400


def test_delete_photo_renumbers_the_rest(me, listing_id):
    ids = [me.post(f"/api/listings/{listing_id}/photos/", photo(name=n), format="json").json()["id"] for n in "abc"]
    assert me.delete(f"/api/listings/{listing_id}/photos/{ids[0]}/").status_code == 204
    photos = me.get(f"/api/listings/{listing_id}/").json()["photos"]
    assert [(p["id"], p["sort_order"]) for p in photos] == [(ids[1], 0), (ids[2], 1)]


# ---------------------------------------------------------------- weekly hours

def put_week(me, lid, days):
    return me.put(f"/api/listings/{lid}/weekly-hours/", {"days": days}, format="json")


def test_every_day_7_to_19_is_the_only_week(me, listing_id):
    """Decision §23 (owner, 2026-11-27): fixed hours. The app no longer offers "समय बदलें"."""
    res = put_week(me, listing_id, [{"weekday": d, "start_hour": 7, "end_hour": 19} for d in range(7)])
    assert res.status_code == 200 and res.json()["has_schedule"] is True


@pytest.mark.parametrize("days", [
    [{"weekday": d, "start_hour": 0, "end_hour": 24} for d in range(7)],  # BUGS.md #19
    [{"weekday": d, "start_hour": 6, "end_hour": 18} for d in range(7)],
    [{"weekday": 0, "start_hour": 7, "end_hour": 19}],
])
def test_other_weeks_are_fixed_hours(me, listing_id, days):
    res = put_week(me, listing_id, days)
    assert res.status_code == 400 and res.json()["error"]["code"] == "fixed_hours"


@pytest.mark.parametrize("days", [
    [],
    [{"weekday": 0, "start_hour": 12, "end_hour": 12}],
    [{"weekday": 0, "start_hour": 18, "end_hour": 6}],
    [{"weekday": 7, "start_hour": 6, "end_hour": 18}],
    [{"weekday": 0, "start_hour": 6, "end_hour": 25}],
    [{"weekday": 0, "start_hour": 6.5, "end_hour": 12}],
    [{"weekday": 1, "start_hour": 6, "end_hour": 12}, {"weekday": 1, "start_hour": 13, "end_hour": 18}],
])
def test_bad_weeks_refused(me, listing_id, days):
    assert put_week(me, listing_id, days).status_code == 400
    # Nothing changed: still the default every day 7–19 (decision §22).
    assert me.get(f"/api/listings/{listing_id}/").json()["weekly_hours"][0] == {"weekday": 0, "start_hour": 7,
                                                                             "end_hour": 19}
