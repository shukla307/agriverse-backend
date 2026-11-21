"""GET /api/search/ (06 §7, 05 §6, roadmap Phase 4 slice 1)."""

import json
import math
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from accounts.models import User
from listings.models import Listing, ListingOption, Photo, WeeklyHours
from listings.search import bounding_box

from .conftest import mt

# Real places (rounded coordinates).
JUNCTION = (25.4447, 81.8282)  # Prayagraj Junction railway station
PHAPHAMAU = (25.5326, 81.8634)  # Phaphamau, across the Ganga to the north
JHUSI = (25.4380, 81.9050)  # Jhunsi, east across the Ganga


def haversine_km(a, b):
    """Plain-Python Haversine: an independent check of the SQL one."""
    (lat1, lng1), (lat2, lng2) = (map(math.radians, a)), (map(math.radians, b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


def point_at(origin, km, bearing_deg):
    """The point `km` away from origin in a compass direction (great-circle formula)."""
    lat1, lng1 = map(math.radians, origin)
    d, b = km / 6371.0, math.radians(bearing_deg)
    lat2 = math.asin(math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(b))
    lng2 = lng1 + math.atan2(math.sin(b) * math.sin(d) * math.cos(lat1), math.cos(d) - math.sin(lat1) * math.sin(lat2))
    return math.degrees(lat2), math.degrees(lng2)


def make(owner, where, machine="tractor", options=(("", "hour", "600"),), **fields):
    """A listing at `where` with options (implement code or "", unit, price or None)."""
    lat, lng = where
    listing = Listing.objects.create(
        owner=owner, machine_type=mt(machine), lat=Decimal(f"{lat:.6f}"), lng=Decimal(f"{lng:.6f}"),
        village=fields.pop("village", "गाँव"), landmark="पीपल के पेड़ के पास", **fields,
    )
    for implement, unit, price in options:
        ListingOption.objects.create(
            listing=listing, implement=mt(implement) if implement else None, price_unit=unit,
            price=Decimal(price) if price else None,
        )
    return listing


@pytest.fixture
def renter(api, stranger):
    api.force_authenticate(stranger)
    return api


def search(client, where=JUNCTION, **params):
    res = client.get("/api/search/", {"lat": where[0], "lng": where[1], **params})
    assert res.status_code == 200, res.json()
    return res.json()


def ids(body):
    return [r["id"] for r in body["results"]]


# ---- input ----

def test_needs_login(api):
    assert api.get("/api/search/", {"lat": 25.4, "lng": 81.8}).status_code == 401


@pytest.mark.parametrize(
    "params, field",
    [
        ({"lng": 81.8}, "lat"),
        ({"lat": 25.4, "lng": 81.8, "radius_km": 101}, "radius_km"),
        ({"lat": 25.4, "lng": 81.8, "job": "flying"}, "job"),
        ({"lat": 25.4, "lng": 81.8, "machine": "spaceship"}, "machine"),
        ({"lat": 25.4, "lng": 81.8, "unit": "week"}, "unit"),
        ({"lat": 25.4, "lng": 81.8, "hp_min": 60, "hp_max": 40}, "hp_max"),
    ],
)
def test_bad_input_is_a_400_with_the_field(renter, params, field):
    res = renter.get("/api/search/", params)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "validation_error"
    assert field in res.json()["error"]["fields"]


# ---- distance ----

def test_distance_matches_an_independent_calculation(renter, owner):
    make(owner, PHAPHAMAU)
    expected = haversine_km(JUNCTION, PHAPHAMAU)
    assert 10.0 < expected < 10.5  # sanity: Junction ↔ Phaphamau is ~10 km in a straight line
    body = search(renter, radius_km=25)
    assert body["results"][0]["distance_km"] == round(expected, 1)


def test_only_inside_the_radius_and_never_far_ones_as_near(renter, owner):
    make(owner, PHAPHAMAU)  # ~10.3 km
    body = search(renter, radius_km=5)
    assert body["count"] == 0 and body["results"] == []
    assert body["hint"] == "none_in_radius"
    assert search(renter, radius_km=25)["hint"] is None


@pytest.mark.parametrize("bearing", [0, 45, 90, 135, 180, 225, 270, 315])
def test_edge_of_the_circle_in_every_direction(renter, owner, bearing):
    """9.9 km in, 10.1 km out, in all 8 directions: the bounding box never cuts off a corner of the circle."""
    inside = make(owner, point_at(JUNCTION, 9.9, bearing))
    make(owner, point_at(JUNCTION, 10.1, bearing))
    assert ids(search(renter, radius_km=10)) == [inside.id]


def test_bounding_box_contains_the_circle():
    lat_min, lat_max, lng_min, lng_max = bounding_box(*JUNCTION, 10)
    for bearing in range(0, 360, 15):
        lat, lng = point_at(JUNCTION, 10, bearing)
        assert lat_min <= Decimal(str(lat)) <= lat_max and lng_min <= Decimal(str(lng)) <= lng_max


def test_nearest_first(renter, owner):
    far = make(owner, PHAPHAMAU)  # ~10.3 km
    near = make(owner, JHUSI)  # ~7.7 km
    nearest = make(owner, point_at(JUNCTION, 1, 90))
    assert ids(search(renter, radius_km=25)) == [nearest.id, near.id, far.id]


# ---- who is shown ----

def test_paused_removed_own_and_disabled_owners_are_hidden(renter, api, owner, stranger):
    shown = make(owner, JHUSI)
    make(owner, JHUSI, status=Listing.Status.PAUSED)
    make(owner, JHUSI, status=Listing.Status.REMOVED)
    make(stranger, JHUSI)  # the renter's own machine
    gone = User.objects.create_user(phone="+919800000001", firebase_uid="uid-gone", name="Gone", is_active=False)
    make(gone, JHUSI)
    assert ids(search(renter)) == [shown.id]


def test_not_bookable_machines_are_listed_and_marked(renter, owner):
    no_hours = make(owner, JHUSI)
    with_hours = make(owner, point_at(JUNCTION, 2, 0))
    WeeklyHours.objects.create(listing=with_hours, weekday=0, start_hour=6, end_hour=18)
    with_hours.has_schedule = True
    with_hours.save()
    by_id = {r["id"]: r for r in search(renter)["results"]}
    assert by_id[no_hours.id]["bookable"] is False
    assert by_id[with_hours.id]["bookable"] is True


# ---- job / machine / unit / HP ----

@pytest.fixture
def mix(owner):
    """Four machines at Jhunsi."""
    return {
        "tractor_rotavator": make(owner, JHUSI, options=[("", "hour", "600"), ("rotavator", "acre", "1500"),
                                                         ("rotavator", "hour", "1000")]),
        "tractor_plain": make(owner, JHUSI, options=[("", "hour", "500")]),
        "rotavator_alone": make(owner, JHUSI, machine="rotavator", options=[("", "day", "2000")]),
        "drone": make(owner, JHUSI, machine="drone_sprayer", options=[("", "acre", "400")]),
    }


def test_machine_finds_it_alone_and_as_an_attachment(renter, mix):
    body = search(renter, machine="rotavator")
    assert set(ids(body)) == {mix["tractor_rotavator"].id, mix["rotavator_alone"].id}
    by_id = {r["id"]: r for r in body["results"]}
    assert by_id[mix["tractor_rotavator"].id]["matched_implement"]["code"] == "rotavator"
    assert by_id[mix["rotavator_alone"].id]["matched_implement"] is None


def test_a_removed_attachment_no_longer_matches(renter, mix):
    mix["tractor_rotavator"].options.filter(implement__code="rotavator").update(is_active=False)
    assert ids(search(renter, machine="rotavator")) == [mix["rotavator_alone"].id]


def test_job_matches_the_machine_or_an_attachment(renter, owner, mix):
    land = search(renter, job="land_prep")
    assert set(ids(land)) == {mix["tractor_rotavator"].id, mix["tractor_plain"].id, mix["rotavator_alone"].id}
    # Tractors do land prep themselves, so nothing is "matched through" an attachment.
    assert all(r["matched_implement"] is None for r in land["results"])

    seeding = make(owner, JHUSI, options=[("", "hour", "600"), ("seed_drill", "acre", "900")])
    sowing = search(renter, job="sowing")
    assert ids(sowing) == [seeding.id]
    assert sowing["results"][0]["matched_implement"]["code"] == "seed_drill"
    assert sowing["results"][0]["from_price"] == {"unit": "acre", "price": "900.00"}


def test_unit_must_be_offered_for_the_matched_attachment(renter, mix):
    assert set(ids(search(renter, machine="rotavator", unit="acre"))) == {mix["tractor_rotavator"].id}
    assert set(ids(search(renter, machine="rotavator", unit="day"))) == {mix["rotavator_alone"].id}
    # The tractor has an hourly price, but not for a rotavator: plain tractor hour price doesn't count.
    assert set(ids(search(renter, unit="day"))) == {mix["rotavator_alone"].id}


def test_hp_filter(renter, owner):
    small = make(owner, JHUSI, hp=30)
    big = make(owner, JHUSI, hp=55)
    make(owner, JHUSI)  # HP not given: left out when filtering by HP
    assert ids(search(renter, hp_min=40)) == [big.id]
    assert ids(search(renter, hp_max=40)) == [small.id]


# ---- the card ----

def test_from_price_prefers_hour_then_lowest(renter, owner):
    make(owner, JHUSI, options=[("", "day", "3000"), ("", "hour", "700"), ("cultivator", "hour", "650")])
    assert search(renter)["results"][0]["from_price"] == {"unit": "hour", "price": "650.00"}


def test_from_price_uses_the_matched_attachment(renter, mix):
    by_id = {r["id"]: r for r in search(renter, machine="rotavator")["results"]}
    assert by_id[mix["tractor_rotavator"].id]["from_price"] == {"unit": "hour", "price": "1000.00"}


def test_negotiable_only(renter, owner):
    make(owner, JHUSI, options=[("", "negotiable", None)])
    assert search(renter)["results"][0]["from_price"] == {"unit": "negotiable", "price": None}


def test_card_fields_and_privacy(renter, owner):
    listing = make(owner, JHUSI, brand="Mahindra", model="575 DI", hp=45, village="झूंसी")
    Photo.objects.create(listing=listing, url="https://x/2_full.jpg", thumb_url="https://x/2_thumb.jpg",
                         storage_path="b", sort_order=1)
    Photo.objects.create(listing=listing, url="https://x/1_full.jpg", thumb_url="https://x/1_thumb.jpg",
                         storage_path="a", sort_order=0)
    owner.name = "Ramesh Kumar Yadav"
    owner.save()

    body = search(renter)
    card = body["results"][0]
    assert card["machine"]["code"] == "tractor" and card["machine"]["icon"] == "ic_tractor"
    assert card["brand"] == "Mahindra" and card["hp"] == 45 and card["village"] == "झूंसी"
    assert card["operator_included"] is True and card["transport_included"] is True
    assert card["thumb_url"] == "https://x/1_thumb.jpg"  # the cover = first photo
    assert card["owner"] == {"first_name": "Ramesh"}

    text = json.dumps(body, ensure_ascii=False)
    for secret in (owner.phone, "पीपल के पेड़ के पास", str(listing.lat), str(listing.lng), "Kumar", "Yadav"):
        assert secret not in text
    for key in ("lat", "lng", "landmark", "phone", "owner_id"):
        assert key not in card


# ---- pages and speed ----

def test_20_per_page(renter, owner):
    for i in range(25):
        make(owner, point_at(JUNCTION, 0.2 * (i + 1), 90))
    first = search(renter)
    assert len(first["results"]) == 20 and first["count"] == 25 and first["next_page"] == 2
    second = search(renter, page=2)
    assert len(second["results"]) == 5 and second["next_page"] is None
    assert not set(ids(first)) & set(ids(second))


def test_same_number_of_queries_for_3_or_20_results(renter, owner):
    """No per-row queries (the old search did one per row for the owner)."""

    def count_queries():
        with CaptureQueriesContext(connection) as ctx:
            search(renter, machine="rotavator")
        return len(ctx)

    def add(n):
        for i in range(n):
            listing = make(owner, point_at(JUNCTION, 0.3 * (i + 1), 45),
                           options=[("", "hour", "600"), ("rotavator", "acre", "1500")])
            Photo.objects.create(listing=listing, url="https://x/f.jpg", thumb_url="https://x/t.jpg",
                                 storage_path=f"p{listing.id}")

    add(3)
    few = count_queries()
    add(17)
    assert count_queries() == few


def vincenty_km(a, b):
    """Distance on the WGS-84 ellipsoid (Vincenty's inverse formula): what map tools measure.
    Haversine treats the Earth as a ball, so it may differ a little; the roadmap allows 1 %."""
    f, big_a = 1 / 298.257223563, 6378137.0
    small_b = (1 - f) * big_a
    u1, u2 = math.atan((1 - f) * math.tan(math.radians(a[0]))), math.atan((1 - f) * math.tan(math.radians(b[0])))
    big_l = lam = math.radians(b[1] - a[1])
    for _ in range(200):
        sin_s = math.hypot(math.cos(u2) * math.sin(lam),
                           math.cos(u1) * math.sin(u2) - math.sin(u1) * math.cos(u2) * math.cos(lam))
        cos_s = math.sin(u1) * math.sin(u2) + math.cos(u1) * math.cos(u2) * math.cos(lam)
        sigma = math.atan2(sin_s, cos_s)
        sin_al = math.cos(u1) * math.cos(u2) * math.sin(lam) / sin_s
        cos2_al = 1 - sin_al**2
        cos_2sm = cos_s - 2 * math.sin(u1) * math.sin(u2) / cos2_al
        c = f / 16 * cos2_al * (4 + f * (4 - 3 * cos2_al))
        prev, lam = lam, big_l + (1 - c) * f * sin_al * (
            sigma + c * sin_s * (cos_2sm + c * cos_s * (-1 + 2 * cos_2sm**2)))
        if abs(lam - prev) < 1e-12:
            break
    u_sq = cos2_al * (big_a**2 - small_b**2) / small_b**2
    k_a = 1 + u_sq / 16384 * (4096 + u_sq * (-768 + u_sq * (320 - 175 * u_sq)))
    k_b = u_sq / 1024 * (256 + u_sq * (-128 + u_sq * (74 - 47 * u_sq)))
    d_sigma = k_b * sin_s * (cos_2sm + k_b / 4 * (cos_s * (-1 + 2 * cos_2sm**2)
                                                  - k_b / 6 * cos_2sm * (-3 + 4 * sin_s**2) * (-3 + 4 * cos_2sm**2)))
    return small_b * k_a * (sigma - d_sigma) / 1000


def test_distance_within_1_percent_of_the_map_ellipsoid(renter, owner):
    lucknow_road = point_at(JUNCTION, 48, 330)  # ~48 km north-west, towards Lucknow
    for where in (PHAPHAMAU, JHUSI, lucknow_road):
        make(owner, where)
    for card in search(renter, radius_km=60)["results"]:
        listing = Listing.objects.get(pk=card["id"])
        true_km = vincenty_km(JUNCTION, (float(listing.lat), float(listing.lng)))
        assert abs(card["distance_km"] - true_km) / true_km < 0.01, (card["distance_km"], true_km)


# ---- q: any spelling (slice 2) ----

@pytest.fixture
def village(owner):
    """Machines at Jhunsi, as different owners would type them."""
    return {
        "mahindra_tractor": make(owner, JHUSI, brand="Mahindra", model="575 DI", hp=45),
        "swaraj_tractor": make(owner, JHUSI, brand="Swaraj", hp=50,
                               options=[("", "hour", "600"), ("rotavator", "acre", "1500")]),
        "rotavator": make(owner, JHUSI, machine="rotavator", options=[("", "hour", "900")]),
        "sugarcane": make(owner, JHUSI, machine="other", machine_type_other="गन्ना हार्वेस्टर"),
        "drone": make(owner, JHUSI, machine="drone_sprayer", options=[("", "acre", "400")]),
    }


@pytest.mark.parametrize("q", ["rotaveter", "ROTAVATOR", "रोटावेटर", "मुझे रोटावेटर चाहिए"])
def test_q_any_spelling_finds_machines_and_attachments(renter, village, q):
    body = search(renter, q=q)
    assert set(ids(body)) == {village["rotavator"].id, village["swaraj_tractor"].id}
    by_id = {r["id"]: r for r in body["results"]}
    assert by_id[village["swaraj_tractor"].id]["matched_implement"]["code"] == "rotavator"
    assert by_id[village["swaraj_tractor"].id]["from_price"] == {"unit": "acre", "price": "1500.00"}


@pytest.mark.parametrize("q", ["tracter", "ट्रेक्टर", "TRACTOR"])
def test_q_tractor_spellings(renter, village, q):
    assert set(ids(search(renter, q=q))) == {village["mahindra_tractor"].id, village["swaraj_tractor"].id}


def test_q_brand_even_misspelt(renter, village):
    assert ids(search(renter, q="mahindra")) == [village["mahindra_tractor"].id]
    for misspelt in ("mahendra", "mhindra", "mahindar"):  # not pieces of "Mahindra": only pg_trgm finds these
        assert ids(search(renter, q=misspelt)) == [village["mahindra_tractor"].id], misspelt
    assert ids(search(renter, q="sonalka")) == []  # nobody here has a Sonalika
    assert ids(search(renter, q="575")) == [village["mahindra_tractor"].id]  # model


def test_q_extra_words_narrow_only_when_something_matches(renter, village):
    assert ids(search(renter, q="swaraj tractor")) == [village["swaraj_tractor"].id]
    # "jaldi" matches nothing: ignored, the tractors still come.
    assert set(ids(search(renter, q="jaldi tractor"))) == {village["mahindra_tractor"].id,
                                                            village["swaraj_tractor"].id}


def test_q_finds_names_typed_under_other(renter, owner, village):
    assert ids(search(renter, q="गन्ना")) == [village["sugarcane"].id]
    assert ids(search(renter, q="गन्ना हारवेस्टर")) == [village["sugarcane"].id]  # misspelt, pg_trgm
    puddler = make(owner, JHUSI, options=[("", "hour", "600")])
    ListingOption.objects.create(listing=puddler, implement=mt("other"), implement_other="पडलर",
                                 price_unit="hour", price=Decimal("800"))
    assert ids(search(renter, q="पडलर")) == [puddler.id]


def test_q_nonsense_finds_nothing_and_says_so(renter, village):
    for q in ("xyzqw", "क्षज्ञ", "मुझे चाहिए"):
        body = search(renter, q=q)
        assert body["count"] == 0 and body["hint"] == "no_match", q


def test_q_far_away_says_widen(renter, owner):
    make(owner, PHAPHAMAU, machine="rotavator")  # ~10.4 km
    assert search(renter, q="rotavator", radius_km=5)["hint"] == "none_in_radius"
    assert search(renter, machine="rotavator", radius_km=5)["hint"] == "none_in_radius"
    assert search(renter, machine="thresher", radius_km=5)["hint"] == "no_match"  # none anywhere


def test_q_with_job_must_match_both(renter, owner, village):
    seeder = make(owner, JHUSI, options=[("", "hour", "600"), ("seed_drill", "acre", "900")])
    # "tractor" + job sowing: only the tractor that has a sowing attachment.
    assert ids(search(renter, q="tractor", job="sowing")) == [seeder.id]


def test_q_job_name_means_its_machines(renter, village):
    assert ids(search(renter, q="छिड़काव")) == [village["drone"].id]


def test_q_query_count_stays_constant(renter, owner):
    def count_queries():
        with CaptureQueriesContext(connection) as ctx:
            search(renter, q="swaraj rotavator")
        return len(ctx)

    def add(n):
        for _ in range(n):
            make(owner, JHUSI, brand="Swaraj", options=[("", "hour", "600"), ("rotavator", "acre", "1500")])

    add(3)
    few = count_queries()
    add(17)
    assert count_queries() == few


def test_hint_says_whether_the_filters_or_the_name_found_nothing(renter, owner):
    make(owner, JHUSI, hp=45, options=[("", "hour", "600")])
    # Only hourly tractors exist: per-day finds nothing anywhere, but would without the filter.
    assert search(renter, q="tractor", unit="day")["hint"] == "no_match_filters"
    assert search(renter, q="tractor", hp_min=60)["hint"] == "no_match_filters"
    # Nonsense stays nonsense, filters or not: removing filters wouldn't help.
    assert search(renter, q="xyzqw", unit="day")["hint"] == "no_match"
    assert search(renter, q="xyzqw")["hint"] == "no_match"


def test_the_empty_answer_names_the_real_cause(renter, owner):
    """BUGS.md #7 + #11 (owner, 2026-11-21): "drone" with the job chip कटाई said "no such machine", then
    "remove the filters" sent farmers into the फ़िल्टर sheet, where the job chip isn't. Each cause has its hint."""
    make(owner, JHUSI, machine="drone_sprayer", options=[("", "acre", "400")])
    make(owner, JHUSI, machine="rotavator", options=[("", "hour", "900")])
    # The job chip hides it.
    assert search(renter, q="drone", job="harvesting")["hint"] == "no_match_job"
    assert search(renter, machine="drone_sprayer", job="harvesting")["hint"] == "no_match_job"
    assert search(renter, q="drone", job="harvesting", unit="acre")["hint"] == "no_match_job"
    # Only the फ़िल्टर sheet hides it (the job is right: a rotavator does जुताई).
    assert search(renter, q="rotavator", job="land_prep", unit="day")["hint"] == "no_match_filters"
    assert search(renter, q="rotavator", unit="day")["hint"] == "no_match_filters"
    # Both hide it: the chip first (one honest step at a time).
    assert search(renter, q="drone", job="harvesting", unit="day")["hint"] == "no_match_job"
    # It exists nowhere: the name is the problem, job or not.
    assert search(renter, q="thresher", job="threshing")["hint"] == "no_match"
    assert search(renter, q="xyzqw", job="harvesting")["hint"] == "no_match"
    # A job chip alone (no machine named) that finds nothing isn't "the job hides it".
    assert search(renter, job="threshing")["hint"] == "no_match"


def test_own_machines_never_count_for_the_hint_either(renter, stranger, owner):
    """BUGS.md #2: the renter's OWN machine farther away must not make the server say "look further"."""
    make(stranger, PHAPHAMAU)  # the renter's own tractor, ~10 km
    body = search(renter, radius_km=5)
    assert body["count"] == 0 and body["hint"] == "no_match"
    assert ids(search(renter, radius_km=25)) == []
