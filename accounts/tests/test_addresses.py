"""Saved places: POST/GET /api/me/addresses/ (S6), docs/06-api.md §3."""

import pytest

from accounts.models import Address, User

pytestmark = pytest.mark.django_db

JHUNSI = {"label": "home", "lat": 25.4380123456, "lng": 81.905, "village": "झूसी", "state": "उत्तर प्रदेश"}


@pytest.fixture
def user():
    return User.objects.create_user(phone="+919876543210", firebase_uid="u1", name="Ramesh")


@pytest.fixture
def me(api, user):
    api.force_authenticate(user)
    return api


def post(api, body):
    return api.post("/api/me/addresses/", body, format="json")


def test_first_place_is_saved_and_becomes_home(me, user):
    res = post(me, {**JHUNSI, "landmark": "  शिव   मंदिर के पास "})
    assert res.status_code == 201
    body = res.json()
    assert body["village"] == "झूसी"
    assert body["landmark"] == "शिव मंदिर के पास"
    assert body["lat"] == pytest.approx(25.438012)
    user.refresh_from_db()
    assert user.home_address_id == body["id"]
    assert me.get("/api/me/").json()["home_address"]["id"] == body["id"]


def test_first_place_becomes_home_even_if_it_is_a_field(me, user):
    res = post(me, {**JHUNSI, "label": "field"})
    user.refresh_from_db()
    assert user.home_address_id == res.json()["id"]


def test_second_place_does_not_replace_home_unless_asked(me, user):
    first = post(me, JHUNSI).json()["id"]
    post(me, {**JHUNSI, "label": "field"})
    user.refresh_from_db()
    assert user.home_address_id == first
    third = post(me, {**JHUNSI, "label": "field", "make_home": True}).json()["id"]
    user.refresh_from_db()
    assert user.home_address_id == third


def test_blank_village_is_fine_the_pin_is_what_matters(me):
    res = post(me, {"label": "home", "lat": 25.43, "lng": 81.9})
    assert res.status_code == 201
    assert res.json()["village"] == ""


@pytest.mark.parametrize(
    "lat,lng",
    [(51.5, -0.12), (5.9, 80), (37.1, 80), (25, 67.9), (25, 98.1)],
)
def test_places_outside_india_are_refused(me, lat, lng):
    res = post(me, {**JHUNSI, "lat": lat, "lng": lng})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"


def test_other_needs_a_name(me):
    res = post(me, {**JHUNSI, "label": "other"})
    assert res.status_code == 400
    assert "label_other" in res.json()["error"]["fields"]
    res = post(me, {**JHUNSI, "label": "other", "label_other": "   "})
    assert res.status_code == 400
    res = post(me, {**JHUNSI, "label": "other", "label_other": "दूसरा खेत"})
    assert res.status_code == 201
    assert res.json()["label_other"] == "दूसरा खेत"


def test_name_is_dropped_for_home_and_field(me):
    res = post(me, {**JHUNSI, "label": "field", "label_other": "ignored"})
    assert res.json()["label_other"] == ""


@pytest.mark.parametrize("bad", [{"label": "shop"}, {"label_other": "x" * 61, "label": "other"}, {"landmark": "x" * 201}])
def test_bad_values_are_refused(me, bad):
    assert post(me, {**JHUNSI, **bad}).status_code == 400


def test_at_most_20_places(me, user):
    for _ in range(Address.MAX_PER_USER):
        assert post(me, JHUNSI).status_code == 201
    res = post(me, JHUNSI)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "too_many_addresses"


def test_list_shows_only_my_places_and_not_deleted(me, user):
    mine = post(me, JHUNSI).json()["id"]
    gone = post(me, {**JHUNSI, "label": "field"}).json()["id"]
    Address.objects.filter(pk=gone).update(is_deleted=True)
    other = User.objects.create_user(phone="+919999999999", firebase_uid="u2")
    Address.objects.create(user=other, label="home", lat=25, lng=81)
    res = me.get("/api/me/addresses/")
    assert [a["id"] for a in res.json()] == [mine]


def test_login_required(api):
    assert post(api, JHUNSI).status_code == 401
    assert api.get("/api/me/addresses/").status_code == 401


def test_me_shows_no_home_before_s6(me):
    assert me.get("/api/me/").json()["home_address"] is None


# ---- PATCH /api/me/addresses/{id}/ (Profile: change the home address, owner 2026-11-21) ----

def patch(api, pk, body):
    return api.patch(f"/api/me/addresses/{pk}/", body, format="json")


def test_change_my_home_moves_the_same_address(me, user):
    """Editing replaces the place; no new row each time (there's a limit of 20)."""
    pk = post(me, {**JHUNSI, "landmark": "पुराना"}).json()["id"]
    res = patch(me, pk, {"lat": 25.5, "lng": 81.85, "village": "फाफामऊ", "district": "प्रयागराज", "landmark": " नया  "})
    assert res.status_code == 200
    body = res.json()
    assert (body["id"], body["village"], body["landmark"]) == (pk, "फाफामऊ", "नया")
    assert body["lat"] == pytest.approx(25.5)
    assert Address.objects.filter(user=user).count() == 1
    user.refresh_from_db()  # the test client keeps this very object as the logged-in user
    home = me.get("/api/me/").json()["home_address"]
    assert (home["id"], home["village"]) == (pk, "फाफामऊ")


def test_change_label_rules_still_apply(me):
    pk = post(me, JHUNSI).json()["id"]
    res = patch(me, pk, {"label": "other"})
    assert res.status_code == 400 and "label_other" in res.json()["error"]["fields"]
    assert patch(me, pk, {"label": "other", "label_other": "दूसरा खेत"}).json()["label_other"] == "दूसरा खेत"
    assert patch(me, pk, {"label": "field"}).json()["label_other"] == ""  # name dropped again
    assert patch(me, pk, {"lat": 40.7, "lng": -74.0}).status_code == 400  # still India only


def test_nobody_can_change_someone_elses_or_a_deleted_address(me, user):
    other = User.objects.create_user(phone="+919999999999", firebase_uid="u2")
    theirs = Address.objects.create(user=other, label="home", lat=25, lng=81, village="उनका")
    assert patch(me, theirs.pk, {"village": "मेरा"}).status_code == 404
    theirs.refresh_from_db()
    assert theirs.village == "उनका"
    gone = post(me, JHUNSI).json()["id"]
    Address.objects.filter(pk=gone).update(is_deleted=True)
    assert patch(me, gone, {"village": "x"}).status_code == 404


def test_patch_needs_login(api):
    assert api.patch("/api/me/addresses/1/", {}, format="json").status_code == 401
