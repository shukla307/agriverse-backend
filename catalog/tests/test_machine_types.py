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
        "fuel": "none", "operator": "never", "asks_transport": False, "attachable": True,
        "takes_attachments": False, "has_brand": False, "acre_price": True,
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


# ---- Per-machine settings (owner, 2026-11-23): the table the owner approved, row by row. ----

# code: (kind, fuel, operator, asks_transport, attachable, takes_attachments, has_brand, acre_price)
APPROVED = {
    "tractor": ("power", "diesel", "always", True, False, True, True, True),
    "mini_tractor": ("power", "diesel", "always", True, False, True, True, True),
    "combine_harvester": ("power", "diesel", "always", True, False, False, True, True),
    "reaper_binder": ("power", "diesel_or_petrol", "always", True, False, False, False, True),
    "power_tiller": ("power", "diesel_or_petrol", "always", True, False, False, True, True),
    "paddy_transplanter": ("power", "diesel_or_petrol", "always", True, False, False, False, True),
    "drone_sprayer": ("power", "battery", "always", True, False, False, False, True),
    # Owner: many threshers have their own engine/motor; a tractor-driven one can still be attached.
    "thresher": ("power", "diesel_or_petrol", "always", True, True, False, False, True),
    "trolley": ("implement", "none", "never", False, True, False, False, False),
    "maize_sheller": ("implement", "none", "never", False, True, False, False, False),
    "other": (None, "ask", "ask", True, False, False, False, True),
}
SETTING_KEYS = ("kind", "fuel", "operator", "asks_transport", "attachable", "takes_attachments", "has_brand",
                "acre_price")
PLAIN_ATTACHMENTS = [
    "rotavator", "cultivator", "plough_mb", "plough_disc", "harrow", "laser_leveller", "seed_drill", "super_seeder",
    "happy_seeder", "potato_planter", "sugarcane_planter", "boom_sprayer", "reaper", "straw_reaper",
    "potato_digger", "baler", "mulcher",
]


def test_every_machine_has_the_approved_settings(me):
    by_code = {m["code"]: m for m in me.get(URL).json()["machine_types"]}
    expected = dict(APPROVED)
    for code in PLAIN_ATTACHMENTS:
        expected[code] = ("implement", "none", "never", False, True, False, False, True)
    assert set(by_code) == set(expected), "a machine is missing a row in the approved table"
    for code, row in expected.items():
        assert tuple(by_code[code][k] for k in SETTING_KEYS) == row, code


def test_thresher_keeps_its_name_only_settings_change(me):
    m = {m["code"]: m for m in me.get(URL).json()["machine_types"]}["thresher"]
    assert m["name_hi"] == "थ्रेशर" and m["kind"] == "power" and m["attachable"] is True


@pytest.mark.parametrize("code,name_hi,jobs,hpa,alias", [
    ("straw_reaper", "भूसा मशीन (स्ट्रॉ रीपर)", {"harvesting", "threshing"}, "1.0", "bhusa machine"),
    ("reaper_binder", "रीपर बाइंडर", {"harvesting"}, "1.0", "reaper binder"),
    ("sugarcane_planter", "गन्ना बोने की मशीन", {"sowing"}, "2.5", "ganna planter"),
    ("maize_sheller", "मक्का शेलर", {"threshing"}, None, "makka sheller"),
])
def test_the_four_new_machines(me, code, name_hi, jobs, hpa, alias):
    m = {m["code"]: m for m in me.get(URL).json()["machine_types"]}[code]
    assert (m["name_hi"], set(m["jobs"]), m["default_hours_per_acre"]) == (name_hi, jobs, hpa)
    assert alias in m["search_aliases"] and m["icon"] == f"ic_{code}"


def test_extra_aliases_on_existing_machines(me):
    by_code = {m["code"]: m for m in me.get(URL).json()["machine_types"]}
    assert {"mini combine", "छोटा कंबाइन"} <= set(by_code["combine_harvester"]["search_aliases"])
    assert {"zero drill", "zero till", "जीरो ड्रिल"} <= set(by_code["seed_drill"]["search_aliases"])
    assert {"straw chopper", "पराली"} <= set(by_code["mulcher"]["search_aliases"])


def test_power_machines_first_then_attachments_then_other(me):
    kinds = [m["kind"] for m in me.get(URL).json()["machine_types"]]
    first_implement = kinds.index("implement")
    assert all(k == "power" for k in kinds[:first_implement])
    assert all(k == "implement" for k in kinds[first_implement:-1]) and kinds[-1] is None
