"""POST /api/listings/ (docs/06-api.md §5)."""

import pytest

from catalog.models import MachineType
from listings.models import Listing

from .conftest import mt, photo, tractor_payload

pytestmark = pytest.mark.django_db
URL = "/api/listings/"


def post(api, body):
    return api.post(URL, body, format="json")


def errors(res):
    return res.json()["error"]["fields"]


def test_login_required(api):
    assert post(api, tractor_payload()).status_code == 401


def test_tractor_with_two_attachments(me, owner):
    res = post(me, tractor_payload())
    assert res.status_code == 201
    body = res.json()
    assert body["machine"]["code"] == "tractor"
    assert body["status"] == "active"
    assert body["operator_included"] is True
    # Decision §22: fixed terms, and bookable at once (every day 7–19).
    assert body["gap_hours"] == 1 and body["transport_included"] is True and body["fuel_included"] is True
    assert body["has_schedule"] is True and body["bookable"] is True
    assert body["weekly_hours"] == [{"weekday": d, "start_hour": 7, "end_hour": 19} for d in range(7)]
    # The place is copied from the home address, including the private landmark (owner sees it).
    assert body["location"]["village"] == "झूंसी"
    assert body["location"]["landmark"] == "मंदिर के पास"
    opts = body["options"]
    assert [(o["implement"] and o["implement"]["code"], o["price_unit"], o["price"]) for o in opts] == [
        (None, "hour", "600.00"), ("rotavator", "hour", "900.00"), ("cultivator", "hour", "800.00"),
    ]
    assert {o["min_hours"] for o in opts} == {1}
    assert Listing.objects.get().owner == owner


def test_one_unit_per_machine(me):
    """Decision §22: the owner picks the unit once; every price is in it."""
    res = post(me, tractor_payload(options=[
        {"price_unit": "hour", "price": "600"},
        {"implement_id": mt("rotavator").id, "price_unit": "acre", "price": "1500"},
    ]))
    assert res.status_code == 400 and res.json()["error"]["fields"]["options"] == ["one_unit"]


def test_per_acre_uses_the_default_hours(me):
    res = post(me, tractor_payload(options=[
        {"price_unit": "acre", "price": "1200", "hours_per_acre": "1.5"},
        {"implement_id": mt("rotavator").id, "price_unit": "acre", "price": "1500"},
    ]))
    assert res.status_code == 201, res.json()
    assert res.json()["options"][1]["hours_per_acre"] == "1.5"  # the rotavator's default (05 §3.4a)


def test_minimum_hours_is_always_1(me):
    res = post(me, tractor_payload(options=[{"price_unit": "hour", "price": "600", "min_hours": 3}]))
    assert res.json()["options"][0]["min_hours"] == 1


def test_operator_included_for_every_power_machine_and_never_for_implements(me):
    for m in MachineType.objects.filter(is_active=True).exclude(code="other"):
        body = {"machine_type_id": m.id, "options": [{"price_unit": "day", "price": "1000"}]}
        res = post(me, body)
        assert res.status_code == 201, (m.code, res.json())
        assert res.json()["operator_included"] is (m.kind == MachineType.Kind.POWER), m.code


def test_there_is_no_operator_field_to_send(me):
    res = post(me, tractor_payload(operator_included=False))
    assert res.status_code == 201
    assert res.json()["operator_included"] is True


def test_implement_must_be_an_implement(me):
    body = tractor_payload(options=[{"implement_id": mt("combine_harvester").id, "price_unit": "hour", "price": "9"}])
    res = post(me, body)
    assert res.status_code == 400
    assert errors(res)["options"]["0"]["implement_id"] == ["not_an_implement"]


def test_other_machine_needs_its_name(me):
    body = {"machine_type_id": mt("other").id, "options": [{"price_unit": "hour", "price": "500"}]}
    assert errors(post(me, body))["machine_type_other"] == ["required"]
    body["machine_type_other"] = "  गन्ना   कटर "
    res = post(me, body)
    assert res.status_code == 201
    assert res.json()["machine_type_other"] == "गन्ना कटर"
    assert res.json()["operator_included"] is False  # kind unknown until admin assigns a type


def test_other_attachment_needs_its_name(me):
    opt = {"implement_id": mt("other").id, "price_unit": "negotiable"}
    res = post(me, tractor_payload(options=[opt]))
    assert errors(res)["options"]["0"]["implement_other"] == ["required"]
    opt["implement_other"] = "पोटैटो मशीन"
    res = post(me, tractor_payload(options=[opt]))
    assert res.status_code == 201
    assert res.json()["options"][0]["implement_other"] == "पोटैटो मशीन"


def test_typed_name_is_dropped_when_not_other(me):
    res = post(me, tractor_payload(machine_type_other="kuch bhi"))
    assert res.json()["machine_type_other"] == ""


def test_price_required_unless_negotiable(me):
    res = post(me, tractor_payload(options=[{"price_unit": "hour"}]))
    assert errors(res)["options"]["0"]["price"] == ["required"]
    res = post(me, tractor_payload(options=[{"price_unit": "negotiable", "price": "500"}]))
    assert res.status_code == 201
    assert res.json()["options"][0]["price"] is None


def test_hours_per_acre_only_for_acre(me):
    opt = {"price_unit": "hour", "price": "600", "hours_per_acre": "1.5"}
    assert errors(post(me, tractor_payload(options=[opt])))["options"]["0"]["hours_per_acre"] == ["only_for_acre"]


def test_acre_without_any_default_needs_the_owners_value(me):
    # Tractor alone has no default hours per acre (05 §3.4a).
    opt = {"price_unit": "acre", "price": "1200"}
    assert errors(post(me, tractor_payload(options=[opt])))["options"]["0"]["hours_per_acre"] == ["required"]
    opt["hours_per_acre"] = "2.5"
    res = post(me, tractor_payload(options=[opt]))
    assert res.json()["options"][0]["hours_per_acre"] == "2.5"


def test_min_hours_only_kept_for_hour_and_negotiable(me):
    opts = [{"price_unit": "day", "price": "3000", "min_hours": 5}]
    res = post(me, tractor_payload(options=opts))
    assert res.json()["options"][0]["min_hours"] == 1


def test_same_attachment_and_unit_twice_is_refused(me):
    opts = [{"price_unit": "hour", "price": "600"}, {"price_unit": "hour", "price": "700"}]
    assert errors(post(me, tractor_payload(options=opts)))["options"] == ["duplicate_option"]


def test_needs_at_least_one_option(me):
    assert "options" in errors(post(me, tractor_payload(options=[])))


def test_implement_only_listing_cannot_have_attachments_but_keeps_min_hp(me):
    body = {"machine_type_id": mt("rotavator").id, "min_tractor_hp": 35, "options": [
        {"implement_id": mt("cultivator").id, "price_unit": "hour", "price": "500"}]}
    assert errors(post(me, body))["options"]["0"]["implement_id"] == ["implement_only_listing"]
    body["options"] = [{"price_unit": "acre", "price": "900"}]
    res = post(me, body)
    assert res.status_code == 201
    assert res.json()["min_tractor_hp"] == 35
    assert res.json()["options"][0]["hours_per_acre"] == "1.5"


def test_min_tractor_hp_is_dropped_for_power_machines(me):
    assert post(me, tractor_payload(min_tractor_hp=40)).json()["min_tractor_hp"] is None


def test_inactive_machine_type_is_refused(me):
    body = tractor_payload(machine_type_id=mt("jcb").id)  # "later"
    assert errors(post(me, body))["machine_type_id"] == ["not_found"]


def test_year_cannot_be_in_the_future(me):
    assert "year" in errors(post(me, tractor_payload(year=2100)))


def test_map_pin_instead_of_an_address(me):
    loc = {"lat": 25.4512345678, "lng": 81.8412345678, "village": "फाफामऊ", "landmark": ""}
    res = post(me, tractor_payload(location=loc))
    assert res.json()["location"]["village"] == "फाफामऊ"
    assert res.json()["location"]["lat"] == 25.451235


def test_pin_outside_india_is_refused(me):
    assert "location" in errors(post(me, tractor_payload(location={"lat": 51.5, "lng": -0.1})))


def test_someone_elses_address_is_refused(me, stranger):
    from accounts.models import Address

    theirs = Address.objects.create(user=stranger, label="home", lat=25, lng=81)
    assert errors(post(me, tractor_payload(address_id=theirs.id)))["address_id"] == ["not_found"]


def test_no_place_at_all_is_refused(api, stranger):
    api.force_authenticate(stranger)  # no home address
    assert errors(post(api, tractor_payload()))["location"] == ["required"]


def test_with_photos_and_weekly_hours(me):
    week = [{"weekday": d, "start_hour": 6, "end_hour": 18} for d in range(7)]
    res = post(me, tractor_payload(photos=[photo(name="a"), photo(name="b")], weekly_hours=week))
    body = res.json()
    assert res.status_code == 201
    assert [p["sort_order"] for p in body["photos"]] == [0, 1]
    assert body["photos"][0]["storage_path"].endswith("a_full.jpg")
    assert body["has_schedule"] is True and body["bookable"] is True
    # Decision §23: an old app's own hours are ignored; every machine is every day 7–19.
    assert body["weekly_hours"] == [{"weekday": d, "start_hour": 7, "end_hour": 19} for d in range(7)]


def test_six_photos_refused(me):
    res = post(me, tractor_payload(photos=[photo(name=str(i)) for i in range(6)]))
    assert res.status_code == 400
    assert Listing.objects.count() == 0


def test_photo_must_be_in_the_callers_own_folder(me):
    res = post(me, tractor_payload(photos=[photo(uid="uid-stranger")]))
    assert errors(res)["photos"][0]["storage_path"] == ["not_own_photo"]


def test_nothing_is_saved_when_anything_is_wrong(me):
    bad = tractor_payload(weekly_hours=[{"weekday": 0, "start_hour": 18, "end_hour": 6}])
    assert post(me, bad).status_code == 400
    assert Listing.objects.count() == 0
