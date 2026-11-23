"""
Booking requests in, booking JSON out (docs/06-api.md §8).

Times go out as IST with "+05:30" (what the farmer means); the app never relies on the phone's time zone.
Privacy (05 §7): phones, the field's landmark/pin and the machine's place are in `contact` ONLY once the
owner has accepted. Before that both sides see first names and villages.
"""

from decimal import Decimal

from rest_framework import serializers

from accounts.models import Address
from catalog.models import JobCategory
from listings.models import Listing

from .availability import IST
from .models import Booking

LAT = dict(max_digits=9, decimal_places=6, min_value=Decimal(Address.LAT_MIN), max_value=Decimal(Address.LAT_MAX))
LNG = dict(max_digits=9, decimal_places=6, min_value=Decimal(Address.LNG_MIN), max_value=Decimal(Address.LNG_MAX))


class QuoteSerializer(serializers.Serializer):
    option_id = serializers.IntegerField()
    start_at = serializers.DateTimeField()
    quantity = serializers.DecimalField(max_digits=5, decimal_places=1)


class FieldSerializer(serializers.Serializer):
    """Where the work happens: one of my saved places, or a pin picked on the map (S5 pick-only)."""

    address_id = serializers.IntegerField(required=False)
    lat = serializers.DecimalField(required=False, **LAT)
    lng = serializers.DecimalField(required=False, **LNG)
    village = serializers.CharField(max_length=120, required=False, allow_blank=True, default="")
    landmark = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")

    def validate(self, attrs):
        if attrs.get("address_id") is not None:
            user = self.context["request"].user
            a = Address.objects.filter(user=user, is_deleted=False, pk=attrs["address_id"]).first()
            if a is None:
                raise serializers.ValidationError({"address_id": "unknown"})
            return {"lat": a.lat, "lng": a.lng, "village": a.village, "landmark": a.landmark}
        if attrs.get("lat") is None or attrs.get("lng") is None:
            raise serializers.ValidationError({"lat": "required"})
        return {k: attrs[k] for k in ("lat", "lng", "village", "landmark")}


class CreateSerializer(QuoteSerializer):
    job = serializers.SlugRelatedField(slug_field="code", queryset=JobCategory.objects.filter(is_active=True))
    job_other = serializers.CharField(max_length=60, required=False, allow_blank=True, default="")
    field = FieldSerializer()

    def validate(self, attrs):
        other = attrs["job_other"].strip()
        if attrs["job"].code == "other" and not other:  # अन्य needs a name (decision §14)
            raise serializers.ValidationError({"job_other": "required"})
        attrs["job_other"] = other if attrs["job"].code == "other" else ""
        return attrs


# --------------------------------------------------------------------- out


def ist(dt) -> str | None:
    return dt.astimezone(IST).isoformat() if dt else None


def money(x) -> str | None:
    return None if x is None else f"{x:.2f}"


def first_name(user) -> str:
    words = (user.name or "").split()
    return words[0] if words else ""


def machine_json(listing: Listing) -> dict:
    m = listing.machine_type
    cover = listing.photos.order_by("sort_order", "id").first()
    return {
        "id": listing.pk,
        "machine": {"code": m.code, "name_hi": m.name_hi, "kind": m.kind, "icon": m.icon},
        "machine_type_other": listing.machine_type_other,
        "thumb_url": cover.thumb_url if cover else "",
        "village": listing.village,
    }


def booking_json(b: Booking, group: list[Booking] | None = None) -> dict:
    """One booking. For a per-day group, `days` lists every day and the totals cover the whole group."""
    group = group or [b]
    o = b.option
    totals = [x.estimated_total for x in group]
    shown = sorted(group, key=lambda x: x.start_at)
    out = {
        "id": b.pk,
        "group_id": str(b.group_id) if b.group_id else None,
        "status": b.status,
        "listing": machine_json(b.listing),
        "option": {
            "id": o.pk,
            "implement": None if o.implement is None else {"code": o.implement.code, "name_hi": o.implement.name_hi},
            "implement_other": o.implement_other,
            "price_unit": b.price_unit,
            "unit_price": money(b.unit_price),
            "hours_per_acre": None if b.hours_per_acre_used is None else str(b.hours_per_acre_used),
        },
        "job": {"code": b.job.code, "name_hi": b.job.name_hi},
        "job_other": b.job_other,
        "start_at": ist(shown[0].start_at),
        "end_at": ist(shown[-1].end_at),
        "hours": b.hours,
        "quantity": str(sum(x.quantity for x in group) if b.group_id else b.quantity),
        "estimated_total": None if any(t is None for t in totals) else money(sum(totals)),
        "days": [{"id": x.pk, "start_at": ist(x.start_at), "end_at": ist(x.end_at)} for x in shown]
        if b.group_id else [],
        "renter": {"name": first_name(b.renter)},
        "owner": {"name": first_name(b.listing.owner)},
        "field": {"village": b.field_village},
        "expires_at": ist(b.expires_at),
        "reason": b.reason,
        "reason_other": b.reason_other,
        "events": [{"to_status": e.to_status, "at": ist(e.created_at)} for e in b.events.all()],
    }
    if b.status in (Booking.Status.ACCEPTED, Booking.Status.COMPLETED):
        listing = b.listing
        out["contact"] = {
            "owner_phone": listing.owner.phone,
            "renter_phone": b.renter.phone,
            "field": {"lat": str(b.field_lat), "lng": str(b.field_lng), "landmark": b.field_landmark},
            "listing_location": {"lat": str(listing.lat), "lng": str(listing.lng), "landmark": listing.landmark},
        }
    return out
