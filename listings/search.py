"""
GET /api/search/ — machines near a point (docs/06-api.md §7, 05-data-model.md §6).

How one search runs:
  1. Bounding box: a cheap square around the point (lat/lng BETWEEN …) that the (status, lat, lng) index can use.
  2. Haversine in SQL: the exact straight-line distance; only rows inside the radius stay. Nearest first.
  3. Job / machine / q / unit / HP filters, as EXISTS sub-queries (no duplicate rows, no per-row queries).
  4. One page of 20, with options, photos and owner fetched in a fixed number of queries.

Never in a result: phone, landmark, exact lat/lng (privacy, 06 §7).
q: catalog/matching.py understands machine names in any spelling; other words are looked for in brand,
model and typed अन्य names (pg_trgm). Not in Phase 4: date / start_at (Phase 5, decision §19).
"""

import math
from dataclasses import dataclass, replace
from decimal import Decimal

from django.contrib.postgres.search import TrigramWordSimilarity
from django.db.models import Exists, F, FloatField, OuterRef, Prefetch, Q, QuerySet, Value
from django.db.models.functions import ASin, Cast, Cos, Greatest, Least, Power, Radians, Sin, Sqrt

from catalog.matching import understand
from catalog.models import MachineType

from .models import Listing, ListingOption, Photo

EARTH_RADIUS_KM = 6371.0
KM_PER_DEGREE_LAT = 111.045  # the same everywhere; a degree of longitude shrinks with cos(latitude)

DEFAULT_RADIUS_KM = 10
MAX_RADIUS_KM = 100

# When a listing has prices in several units, "from ₹…" shows the first unit in this order that has a price.
UNIT_ORDER = ("hour", "acre", "day")

# pg_trgm word similarity needed for a misspelt brand / typed name to count. Measured on "Mahindra 575 DI":
# mahendra 0.50, mhindra 0.63, mahindar 0.67, sonalka~Sonalika 0.63. Trade-off: short look-alikes also pass
# (mahila 0.57), so an odd word can bring an extra card; missing a real misspelling is worse for the farmer.
TEXT_SIMILARITY = 0.5


@dataclass(frozen=True)
class SearchParams:
    lat: float
    lng: float
    radius_km: float = DEFAULT_RADIUS_KM
    job: str | None = None
    machine: str | None = None
    q: str | None = None
    unit: str | None = None
    hp_min: int | None = None
    hp_max: int | None = None
    # Filled in by resolve(): each set is "the machine OR an attachment must be one of these codes".
    code_filters: tuple[frozenset[str], ...] = ()
    text_words: tuple[str, ...] = ()  # q's words that are no machine: brand, model, typed अन्य names
    q_understood: bool = True


def resolve(p: SearchParams) -> SearchParams:
    """Turn job / machine / q into code sets once per request (2–3 small catalogue queries)."""
    filters = []
    if p.job:
        filters.append(frozenset(MachineType.objects.filter(jobs__code=p.job).values_list("code", flat=True)))
    if p.machine:
        filters.append(frozenset({p.machine}))
    words, understood = (), True
    if p.q:
        u = understand(p.q)
        if u.machine_codes:
            filters.append(frozenset(u.machine_codes))
        words, understood = tuple(u.rest), not u.empty
    return replace(p, code_filters=tuple(filters), text_words=words, q_understood=understood)


def bounding_box(lat: float, lng: float, radius_km: float) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    """A square that surely contains the circle. Slightly bigger is fine: Haversine decides afterwards."""
    dlat = radius_km / KM_PER_DEGREE_LAT
    # Near the poles cos() → 0; India is far from them, but never divide by zero.
    dlng = radius_km / (KM_PER_DEGREE_LAT * max(math.cos(math.radians(lat)), 0.01))

    def d(x):
        return Decimal(str(round(x, 6)))

    return d(lat - dlat), d(lat + dlat), d(lng - dlng), d(lng + dlng)


def haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """The same formula as haversine_sql, for one listing (details page)."""
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(min(h, 1.0)))


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


def _text_match(word: str):
    """One word of q found in the owner's own text: brand, model, a typed अन्य machine or attachment name.
    'contains' for exact pieces, pg_trgm word similarity for misspellings ("mahindr", "हारवेस्टर")."""
    sim = Greatest(*(TrigramWordSimilarity(Value(word), f) for f in ("brand", "model", "machine_type_other")))
    typed_attachment = ListingOption.objects.filter(listing=OuterRef("pk"), is_active=True).filter(
        Q(implement_other__icontains=word)
        | Q(pk__in=ListingOption.objects.alias(s=TrigramWordSimilarity(Value(word), "implement_other"))
            .filter(s__gte=TEXT_SIMILARITY).values("pk"))
    )
    contains = Q(brand__icontains=word) | Q(model__icontains=word) | Q(machine_type_other__icontains=word)
    return contains | Q(pk__in=Listing.objects.alias(s=sim).filter(s__gte=TEXT_SIMILARITY).values("pk")) | Exists(
        typed_attachment)


def _base(p: SearchParams, exclude_owner, *, radius=True) -> QuerySet:
    qs = Listing.objects.filter(status=Listing.Status.ACTIVE, owner__is_active=True)
    if radius:
        lat_min, lat_max, lng_min, lng_max = bounding_box(p.lat, p.lng, p.radius_km)
        qs = qs.filter(lat__range=(lat_min, lat_max), lng__range=(lng_min, lng_max))
    if exclude_owner is not None:
        qs = qs.exclude(owner=exclude_owner)  # your own machines aren't something to rent

    opts = _matching_options(p)
    for codes in p.code_filters:
        # The machine itself (any of its prices) OR a listing offering it as an attachment.
        qs = qs.filter((Q(machine_type__code__in=codes) & Exists(opts)) | Exists(opts.filter(implement__code__in=codes)))
    if p.unit and not p.code_filters:
        qs = qs.filter(Exists(opts))
    if p.hp_min is not None:
        qs = qs.filter(hp__gte=p.hp_min)
    if p.hp_max is not None:
        qs = qs.filter(hp__lte=p.hp_max)
    if radius:
        qs = qs.annotate(distance_km=haversine_sql(p.lat, p.lng)).filter(distance_km__lte=p.radius_km)
    return qs


def _with_text(qs: QuerySet, p: SearchParams) -> QuerySet:
    for word in p.text_words:
        qs = qs.filter(_text_match(word))
    return qs


def search_queryset(p: SearchParams, *, exclude_owner=None) -> QuerySet:
    """`p` must come from resolve(). Extra words narrow the result ("mahindra tractor"), but if they'd leave
    nothing while a machine WAS understood, they're ignored ("jaldi tractor" still finds tractors)."""
    if p.q and not p.q_understood:
        return Listing.objects.none()
    qs = _base(p, exclude_owner)
    if p.text_words:
        narrowed = _with_text(qs, p)
        qs = narrowed if not p.code_filters or narrowed.exists() else qs
    return (
        qs.order_by("distance_km", "id")
        .select_related("machine_type", "owner")
        .prefetch_related(
            Prefetch(
                "options",
                queryset=ListingOption.objects.filter(is_active=True).select_related("implement"),
                to_attr="active_options",
            ),
            Prefetch("photos", queryset=Photo.objects.order_by("sort_order", "id"), to_attr="ordered_photos"),
        )
    )


def exists_anywhere(p: SearchParams, *, exclude_owner=None) -> bool:
    """Same search without the circle: tells "nothing near you" apart from "nothing like this at all"."""
    if p.q and not p.q_understood:
        return False
    qs = _base(p, exclude_owner, radius=False)
    return (_with_text(qs, p) if p.text_words and not p.code_filters else qs).exists()


# ---- per-result details, from the prefetched rows (no extra queries) ----

def _unmatched_filters(listing: Listing, p: SearchParams) -> list[frozenset[str]]:
    """The asked code sets the machine itself is NOT in, so an attachment must have answered them."""
    return [codes for codes in p.code_filters if listing.machine_type.code not in codes]


def _relevant(listing: Listing, p: SearchParams) -> list[ListingOption]:
    """The options that answer this search (right attachment, right unit)."""
    opts = [o for o in listing.active_options if not p.unit or o.price_unit == p.unit]
    for codes in _unmatched_filters(listing, p):
        opts = [o for o in opts if o.implement and o.implement.code in codes]
    return opts


def matched_implement(listing: Listing, p: SearchParams) -> MachineType | None:
    """The attachment that made this listing match, when the machine itself didn't (06 §7)."""
    if not _unmatched_filters(listing, p):
        return None
    return next((o.implement for o in _relevant(listing, p) if o.implement), None)


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
