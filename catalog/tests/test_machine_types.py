"""GET /api/machine-types/ (docs/06-api.md §4). The seed data comes from the migration."""

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from catalog.models import JobCategory, MachineType

pytestmark = pytest.mark.django_db

URL = "/api/machine-types/"


@pytest.fixture
def me(api):
    api.force_authenticate(User.objects.create_user(phone="+919876543210", firebase_uid="u1"))
    return api


def test_login_required(api):
    assert api.get(URL).status_code == 401


def test_six_jobs_in_order_then_other_last(me):
    jobs = me.get(URL).json()["jobs"]
    assert [j["code"] for j in jobs] == [
        "land_prep", "sowing", "spraying", "harvesting", "threshing", "transport", "other",
    ]
    assert [j["name_hi"] for j in jobs][:6] == ["जुताई", "बुवाई", "छिड़काव", "कटाई", "गहाई", "ढुलाई"]
    assert all(j["icon"] == f"ic_{j['code']}" for j in jobs)


def test_machine_shape_from_the_api_doc(me):
    body = me.get(URL).json()
    by_code = {m["code"]: m for m in body["machine_types"]}
    assert by_code["tractor"]["kind"] == "power"
    assert set(by_code["tractor"]["jobs"]) == {"transport", "land_prep"}
    assert "ट्रेक्टर" in by_code["tractor"]["search_aliases"]
    assert by_code["rotavator"] == {
        "id": by_code["rotavator"]["id"], "code": "rotavator", "name_hi": "रोटावेटर", "kind": "implement",
        "jobs": ["land_prep"], "search_aliases": ["rotavator", "rotaveter", "rotavetor", "rotawator"],
        "icon": "ic_rotavator", "default_hours_per_acre": "1.5",
    }
    # "अन्य" is always there and always last; JCB is "later", so it's hidden.
    assert body["machine_types"][-1]["code"] == "other"
    assert body["machine_types"][-1]["kind"] is None
    assert "jcb" not in by_code


def test_every_job_except_other_has_machines(me):
    body = me.get(URL).json()
    used = {j for m in body["machine_types"] for j in m["jobs"]}
    assert used == {j["code"] for j in body["jobs"]} - {"other"}


def test_same_list_gives_304_and_no_body(me):
    first = me.get(URL)
    etag = first["ETag"]
    assert first.json()["version"] in etag
    again = me.get(URL, HTTP_IF_NONE_MATCH=etag)
    assert again.status_code == 304
    assert again.content == b""
    assert again["ETag"] == etag


def test_admin_edit_changes_the_version(me):
    etag = me.get(URL)["ETag"]
    MachineType.objects.filter(code="harrow").update(name_hi="हैरो (डिस्क)")
    res = me.get(URL, HTTP_IF_NONE_MATCH=etag)
    assert res.status_code == 200
    assert res["ETag"] != etag


def test_hidden_job_is_left_out_everywhere(me):
    JobCategory.objects.filter(code="threshing").update(is_active=False)
    body = me.get(URL).json()
    assert "threshing" not in [j["code"] for j in body["jobs"]]
    assert all("threshing" not in m["jobs"] for m in body["machine_types"])


def test_query_count_does_not_grow_with_the_list(me):
    with CaptureQueriesContext(connection) as ctx:
        me.get(URL)
    assert len(ctx.captured_queries) <= 4


def test_aliases_are_saved_lower_case_without_repeats():
    m = MachineType.objects.create(code="x", name_hi="एक्स", search_aliases=["  Big  Tractor ", "big tractor", "BIG"])
    assert m.search_aliases == ["big tractor", "big"]
