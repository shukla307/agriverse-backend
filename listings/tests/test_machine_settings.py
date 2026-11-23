"""
Each machine type asks only what makes sense (owner, 2026-11-23): the catalogue's settings decide which
answers a listing keeps (fuel, transport, operator), which attachments it may have, and whether एकड़ prices
are allowed. Whatever doesn't apply is stored as null, so S10 shows no badge for it.
"""

import pytest

from catalog.models import MachineType
from listings.models import Listing
from listings.moving import move_to_type

from .conftest import mt, tractor_payload

pytestmark = pytest.mark.django_db
URL = "/api/listings/"


def body_for(code, options=None, **extra):
    body = {"machine_type_id": mt(code).id, "options": options or [{"price_unit": "hour", "price": "900"}]}
    body.update(extra)
    return body


def create(me, body):
    res = me.post(URL, body, format="json")
    assert res.status_code == 201, res.json()
    return res.json()


def field_errors(res):
    assert res.status_code == 400, res.json()
    return res.json()["error"]["fields"]


def answers(body):
    return body["fuel_included"], body["transport_included"], body["operator_included"]


# ---- fuel / transport / operator ----

def test_drone_runs_on_battery_so_no_fuel_answer_is_kept(me):
    body = create(me, body_for("drone_sprayer", fuel_included=True, transport_included=False))
    assert answers(body) == (None, False, True)
    assert body["machine"]["fuel"] == "battery"


def test_attachment_alone_keeps_no_fuel_transport_or_operator(me):
    # The renter's own tractor pulls it and fetches it: none of the three questions apply.
    body = create(me, body_for("rotavator", fuel_included=True, transport_included=True, operator_included=True,
                                min_tractor_hp=35))
    assert answers(body) == (None, None, False)
    assert body["min_tractor_hp"] == 35


def test_tractor_gets_the_wizard_defaults_when_nothing_is_sent(me):
    body = create(me, body_for("tractor"))
    assert answers(body) == (False, True, True)
    assert body["machine"]["fuel"] == "diesel"


def test_tractor_keeps_its_answers(me):
    assert answers(create(me, tractor_payload(fuel_included=True, transport_included=False))) == (True, False, True)


@pytest.mark.parametrize("code", ["power_tiller", "paddy_transplanter", "reaper_binder", "thresher"])
def test_diesel_or_petrol_machines_keep_the_fuel_answer(me, code):
    body = create(me, body_for(code, fuel_included=True))
    assert answers(body) == (True, True, True)
    assert body["machine"]["fuel"] == "diesel_or_petrol"


def test_thresher_alone_is_a_power_machine_now(me):
    body = create(me, body_for("thresher", min_tractor_hp=35))
    assert body["machine"]["kind"] == "power" and body["operator_included"] is True
    assert body["min_tractor_hp"] is None  # only attachments listed alone need the renter's tractor


def test_other_machine_owner_answers_all_three(me):
    body = create(me, body_for("other", machine_type_other="गन्ना कोल्हू",
                                fuel_included=None, transport_included=False, operator_included=True))
    assert answers(body) == (None, False, True)  # None = "तेल नहीं लगता"
    body = create(me, body_for("other", machine_type_other="पानी का पंप", fuel_included=True))
    assert answers(body) == (True, True, False)  # missing operator answer → no operator line


def test_patch_cannot_add_an_answer_the_machine_does_not_ask(me):
    listing_id = create(me, body_for("drone_sprayer"))["id"]
    res = me.patch(f"{URL}{listing_id}/", {"fuel_included": True, "operator_included": False}, format="json")
    assert res.status_code == 200
    assert answers(res.json()) == (None, True, True)


def test_patch_changes_an_answer_the_machine_asks(me, listing_id):
    res = me.patch(f"{URL}{listing_id}/", {"fuel_included": True}, format="json")
    assert res.json()["fuel_included"] is True
    # Untouched answers stay as they were.
    assert res.json()["transport_included"] is True


def test_renter_view_has_the_fuel_word_and_null_badges(me, api, stranger):
    listing_id = create(me, body_for("rotavator"))["id"]
    api.force_authenticate(stranger)
    body = api.get(f"{URL}{listing_id}/").json()
    assert body["machine"]["fuel"] == "none"
    assert (body["fuel_included"], body["transport_included"], body["operator_included"]) == (None, None, False)


# ---- attachments ----

def test_thresher_can_still_be_added_to_a_tractor(me):
    body = create(me, tractor_payload(options=[
        {"price_unit": "hour", "price": "600"},
        {"implement_id": mt("thresher").id, "price_unit": "hour", "price": "1200"},
    ]))
    assert [o["implement"]["code"] if o["implement"] else "" for o in body["options"]] == ["", "thresher"]


@pytest.mark.parametrize("code", ["combine_harvester", "drone_sprayer", "thresher", "reaper_binder"])
def test_only_tractors_take_attachments(me, code):
    res = me.post(URL, body_for(code, options=[
        {"price_unit": "hour", "price": "900"},
        {"implement_id": mt("rotavator").id, "price_unit": "hour", "price": "1000"},
    ]), format="json")
    assert field_errors(res)["options"]["1"]["implement_id"] == ["no_attachments"]


def test_a_power_machine_is_not_an_attachment(me):
    res = me.post(URL, tractor_payload(options=[
        {"implement_id": mt("combine_harvester").id, "price_unit": "hour", "price": "1000"},
    ]), format="json")
    assert field_errors(res)["options"]["0"]["implement_id"] == ["not_an_implement"]


@pytest.mark.parametrize("code", ["straw_reaper", "sugarcane_planter", "maize_sheller"])
def test_new_attachments_can_be_added_to_a_tractor(me, code):
    create(me, tractor_payload(options=[{"implement_id": mt(code).id, "price_unit": "hour", "price": "1500"}]))


# ---- per-acre prices ----

def test_no_acre_price_for_a_trolley_on_a_tractor(me):
    res = me.post(URL, tractor_payload(options=[
        {"implement_id": mt("trolley").id, "price_unit": "acre", "price": "500", "hours_per_acre": "1"},
    ]), format="json")
    assert field_errors(res)["options"]["0"]["price_unit"] == ["no_acre"]


def test_no_acre_price_for_a_maize_sheller_alone(me):
    res = me.post(URL, body_for("maize_sheller", options=[
        {"price_unit": "acre", "price": "500", "hours_per_acre": "1"}]), format="json")
    assert field_errors(res)["options"]["0"]["price_unit"] == ["no_acre"]


def test_tractor_alone_per_acre_is_still_fine(me):
    create(me, tractor_payload(options=[{"price_unit": "acre", "price": "1200", "hours_per_acre": "1.5"}]))


def test_new_machine_per_acre_uses_its_default_hours(me):
    body = create(me, body_for("reaper_binder", options=[{"price_unit": "acre", "price": "1800"}]))
    assert body["options"][0]["hours_per_acre"] == "1.0"


# ---- admin "move अन्य to a real type" follows the target's settings ----

def test_moved_other_machine_follows_the_new_types_settings(me):
    listing = Listing.objects.get(pk=create(me, body_for(
        "other", machine_type_other="धान की पुआल मशीन", fuel_included=True, transport_included=True,
        operator_included=True))["id"])
    move_to_type([listing], mt("mulcher"))
    listing.refresh_from_db()
    assert (listing.fuel_included, listing.transport_included, listing.operator_answer) == (None, None, None)
    assert listing.operator_included is False


def test_moved_other_machine_keeps_answers_the_new_type_asks(me):
    listing = Listing.objects.get(pk=create(me, body_for(
        "other", machine_type_other="बाइंडर", fuel_included=True, transport_included=False))["id"])
    move_to_type([listing], mt("reaper_binder"))
    listing.refresh_from_db()
    assert (listing.fuel_included, listing.transport_included) == (True, False)
    assert listing.operator_included is True


def test_settings_are_editable_in_admin_without_an_app_update():
    MachineType.objects.filter(code="power_tiller").update(fuel=MachineType.Fuel.DIESEL)
    assert mt("power_tiller").fuel == "diesel"
