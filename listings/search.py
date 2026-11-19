"""
GET /api/search/ — machines near a point (docs/06-api.md §7, 05-data-model.md §6).

How one search runs:
  1. Bounding box: a cheap square around the point (lat/lng BETWEEN …) that the (status, lat, lng) index can use.
  2. Haversine in SQL: the exact straight-line distance; only rows inside the radius stay. Nearest first.
  3. Job / machine / unit / HP filters, as EXISTS sub-queries (no duplicate rows, no per-row queries).
  4. One page of 20, with options, photos and owner fetched in a fixed number of queries.

Never in a result: phone, landmark, exact lat/lng (privacy, 06 §7).
Not in Phase 4: q (slice 2), date / start_at (Phase 5, decision §19).
"""

import math
from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Exists, F, FloatField, OuterRef, Prefetch, Q, QuerySet, Value
from django.db.models.functions import ASin, Cast, Cos, Least, Power, Radians, Sin, Sqrt

from catalog.models import MachineType

from .models import Listing, ListingOption, Photo

EARTH_RADIUS_KM = 6371.0
KM_PER_DEGREE_LAT = 111.045  # the same everywhere; a degree of longitude shrinks with cos(latitude)

DEFAULT_RADIUS_KM = 10
MAX_RADIUS_KM = 100

# When a listing has prices in several units, "from ₹…" shows the first unit in this order that has a price.
UNIT_ORDER = ("hour", "acre", "day")


@dataclass(frozen=True)
class SearchParams:
    lat: float
    lng: float
    radius_km: float = DEFAULT_RADIUS_KM
    job: str | None = None
    machine: str | None = None
    unit: str | None = None
    hp_min: int | None = None
    hp_max: int | None = None


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """A square that surely contains the circle. Slightly bigger is fine: Haversine decides afterwards."""
    dlat = radius_km / KM_PER_DEGREE_LAT
    # Near the poles cos() → 0; India is far from them, but never divide by zero.
    dlng = radius_km / (KM_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.01))

    def d(x):
        return Decimal(str(round(x, 6)))

    return d(lat - dlat), d(lat + dlat), d(lng - dlng), d(lng + dlng)


def haversine_sql(lat: float, lng: float):
    """Straight-line km from (lat, lng) to each listing's pin, computed by PostgreSQL."""
    lat1, lng1 = Radians(Value(lat, FloatField())), Radians(Value(lng, FloatField()))
    lat2 = Radians(Cast(F("lat"), FloatField()))
    lng2 = Radians(Cast(F("lng"), FloatField()))
    a = Power(Sin((lat2 - lat1) / 2), 2) + Cos(lat1) * Cos(lat2) * Power(Sin((lng2 - lng1) / 2), 2)
    # Least(…, 1): rounding can push a tiny bit over 1 for opposite points, and asin(>1) is an SQL error.
    return 2 * EARTH_RADIUS_KM * ASin(Sqrt(Least(a, Value(1.0, FloatField()))))


def _matching_options(p: SearchParams):
    """Active options of the outer listing, limited to the asked unit."""
    opts = ListingOption.objects.filter(listing=OuterRef("pk"), is_active=True)
    return opts.filter(price_unit=p.unit) if p.unit else opts


def search_queryset(p: SearchParams, *, exclude_owner=None) -> QuerySet:
    lat_min, lat_max, lng_min, lng_max = bounding_box(p.lat, p.lng, p.radius_km)
    qs = Listing.objects.filter(
        status=Listing.Status.ACTIVE,
        owner__is_active=True,
        lat__range=(lat_min, lat_max),
        lng__range=(lng_min, lng_max),
    )
    if exclude_owner is not None:
        qs = qs.exclude(owner=exclude_owner)  # your own machines aren't something to rent

    opts = _matching_options(p)
    if p.machine:
        # The machine itself (any of its prices) OR a listing offering it as an attachment.
        qs = qs.filter(
            (Q(machine_type__code=p.machine) & Exists(opts)) | Exists(opts.filter(implement__code=p.machine))
        )
    elif p.job:
        types_for_job = MachineType.objects.filter(jobs__code=p.job).values("pk")
        qs = qs.filter(
            (Q(machine_type__in=types_for_job) & Exists(opts)) | Exists(opts.filter(implement__in=types_for_job))
        )
    elif p.unit:
        qs = qs.filter(Exists(opts))

    if p.hp_min is not None:
        qs = qs.filter(hp__gte=p.hp_min)
    if p.hp_max is not None:
        qs = qs.filter(hp__lte=p.hp_max)

    return (
        qs.annotate(distance_km=haversine_sql(p.lat, p.lng))
        .filter(distance_km__lte=p.radius_km)
        .order_by("distance_km", "id")
        .select_related("machine_type", "owner")
        .prefetch_related(
            "machine_type__jobs",
            Prefetch(
                "options",
                queryset=ListingOption.objects.filter(is_active=True).select_related("implement"),
                to_attr="active_options",
            ),
            "active_options__implement__jobs",
            Prefetch("photos", queryset=Photo.objects.order_by("sort_order", "id"), to_attr="ordered_photos"),
        )
    )


# ---- per-result details, from the prefetched rows (no extra queries) ----

def matched_implement(listing: Listing, p: SearchParams) -> MachineType | None:
    """The attachment that made this listing match, when the machine itself didn't (06 §7)."""
    if p.machine:
        if listing.machine_type.code == p.machine:
            return None
        return next((o.implement for o in _relevant(listing, p) if o.implement), None)
    if p.job:
        if any(j.code == p.job for j in listing.machine_type.jobs.all()):
            return None
        return next((o.implement for o in _relevant(listing, p) if o.implement), None)
    return None


def _relevant(listing: Listing, p: SearchParams) -> list[ListingOption]:
    """The options that answer this search (right attachment, right unit)."""
    opts = [o for o in listing.active_options if not p.unit or o.price_unit == p.unit]
    if p.machine and listing.machine_type.code != p.machine:
        opts = [o for o in opts if o.implement and o.implement.code == p.machine]
    elif p.job and not any(j.code == p.job for j in listing.machine_type.jobs.all()):
        opts = [o for o in opts if o.implement and any(j.code == p.job for j in o.implement.jobs.all())]
    return opts


def from_price(listing: Listing, p: SearchParams) -> dict | None:
    """'From ₹600 / घंटा': the lowest price among the relevant options, in the first unit of UNIT_ORDER
    that has one. Only मोल-भाव → {"unit": "negotiable", "price": null}."""
    opts = _relevant(listing, p) or listing.active_options
    for unit in UNIT_ORDER:
        prices = [o.price for o in opts if o.price_unit == unit and o.price is not None]
        if prices:
            return {"unit": unit, "price": f"{min(prices):.2f}"}
    if any(o.price_unit == ListingOption.Unit.NEGOTIABLE for o in opts):
        return {"unit": "negotiable", "price": None}
    return None
