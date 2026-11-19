"""'New machine types to add' (05 §3.4): admin moves अन्य listings to a real type."""

import pytest

from accounts.models import User
from catalog.models import MachineType
from listings.models import Listing, ListingOption, NewTypeRequest
from listings.moving import move_to_type

from .conftest import mt, tractor_payload

pytestmark = pytest.mark.django_db


@pytest.fixture
def other_machine(me):
    body = {"machine_type_id": mt("other").id, "machine_type_other": "गन्ना हार्वेस्टर",
            "options": [{"price_unit": "hour", "price": "2000"}]}
    return Listing.objects.get(pk=me.post("/api/listings/", body, format="json").json()["id"])


@pytest.fixture
def other_attachment(me):
    body = tractor_payload(options=[
        {"price_unit": "hour", "price": "600"},
        {"implement_id": mt("other").id, "implement_other": "रिज मेकर", "price_unit": "hour", "price": "900"},
    ])
    return Listing.objects.get(pk=me.post("/api/listings/", body, format="json").json()["id"])


def test_only_other_listings_appear_in_the_admin_list(me, other_machine, other_attachment, listing_id):
    from django.contrib.admin.sites import site

    from listings.admin import NewTypeRequestAdmin

    qs = NewTypeRequestAdmin(NewTypeRequest, site).get_queryset(None)
    # The plain tractor (listing_id) has no अन्य, so it isn't listed.
    assert set(qs.values_list("pk", flat=True)) == {other_machine.pk, other_attachment.pk}


def test_move_other_machine_to_new_type(other_machine):
    new = MachineType.objects.create(code="sugarcane_harvester", name_hi="गन्ना हार्वेस्टर", kind="power")
    assert move_to_type([other_machine], new) == (1, 0)
    other_machine.refresh_from_db()
    assert other_machine.machine_type == new and other_machine.machine_type_other == ""
    # Found by the new type from now on (search in Phase 4 filters exactly like this):
    assert Listing.objects.filter(machine_type__code="sugarcane_harvester").get() == other_machine


def test_move_other_attachment_only_to_an_implement(other_attachment):
    tractor = mt("tractor")
    assert move_to_type([other_attachment], tractor) == (0, 0)  # a power machine is not an attachment
    ridger = MachineType.objects.create(code="ridger", name_hi="रिज मेकर", kind="implement")
    assert move_to_type([other_attachment], ridger) == (0, 1)
    opt = other_attachment.options.get(implement=ridger)
    assert opt.implement_other == ""
    assert ListingOption.objects.filter(is_active=True, implement__code="ridger").count() == 1


def test_move_keeps_owners_own_option_when_it_would_clash(me):
    body = tractor_payload(options=[
        {"implement_id": mt("rotavator").id, "price_unit": "hour", "price": "1000"},
        {"implement_id": mt("other").id, "implement_other": "rotavetar", "price_unit": "hour", "price": "999"},
    ])
    listing = Listing.objects.get(pk=me.post("/api/listings/", body, format="json").json()["id"])
    move_to_type([listing], mt("rotavator"))
    active = listing.options.filter(is_active=True)
    assert [(o.implement.code, str(o.price)) for o in active] == [("rotavator", "1000.00")]


def test_cannot_move_to_other(other_machine):
    with pytest.raises(ValueError):
        move_to_type([other_machine], mt("other"))


def test_admin_page_and_action_work(client, other_machine):
    admin = User.objects.create_superuser(phone="+919000000001", password="a-strong-pass-123")
    client.force_login(admin)
    assert client.get("/admin/listings/newtyperequest/").status_code == 200
    page = client.post("/admin/listings/newtyperequest/",
                       {"action": "move_to_real_type", "_selected_action": [other_machine.pk]})
    assert page.status_code == 200 and "Move to machine type" in page.content.decode()
    new = MachineType.objects.create(code="sugarcane_harvester", name_hi="गन्ना हार्वेस्टर", kind="power")
    client.post("/admin/listings/newtyperequest/", {
        "action": "move_to_real_type", "_selected_action": [other_machine.pk], "apply": "1", "target": new.pk})
    other_machine.refresh_from_db()
    assert other_machine.machine_type == new
