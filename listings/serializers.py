"""
What the app may send and what it gets back (docs/06-api.md §5–§6).
All the rules for "is this listing sensible?" live here, in one place.
"""

import datetime
from decimal import Decimal

from rest_framework import serializers

from accounts.models import Address
from catalog.models import JobCategory, MachineType
from core.storage import bucket_path

from .models import Listing, ListingOption, Photo, WeeklyHours
from .search import from_price, haversine_km, matched_implement

OTHER = "other"


def tidy(text: str) -> str:
    """'  गन्ना   कटर ' → 'गन्ना कटर'"""
    return " ".join((text or "").split())


# --------------------------------------------------------------------- weekly hours


class WeeklyHoursSerializer(serializers.Serializer):
    weekday = serializers.IntegerField(min_value=0, max_value=6)
    start_hour = serializers.IntegerField(min_value=0, max_value=23)
    end_hour = serializers.IntegerField(min_value=1, max_value=24)

    def validate(self, attrs):
        if attrs["end_hour"] <= attrs["start_hour"]:
            raise serializers.ValidationError({"end_hour": "must_be_after_start"})
        return attrs


def validate_week(days: list[dict]) -> list[dict]:
    """At least one day, each weekday once."""
    if not days:
        raise serializers.ValidationError("at_least_one_day")
    weekdays = [d["weekday"] for d in days]
    if len(weekdays) != len(set(weekdays)):
        raise serializers.ValidationError("weekday_twice")
    return sorted(days, key=lambda d: d["weekday"])


class WeekSerializer(serializers.Serializer):
    """PUT /api/listings/{id}/weekly-hours/"""

    days = WeeklyHoursSerializer(many=True)

    def validate_days(self, days):
        return validate_week(days)


# --------------------------------------------------------------------- options


class OptionWriteSerializer(serializers.Serializer):
    """One way to book the machine. Context: "machine_type" (the listing's), "partial" for PATCH."""

    implement_id = serializers.IntegerField(required=False, allow_null=True)
    implement_other = serializers.CharField(max_length=60, required=False, allow_blank=True, trim_whitespace=True)
    price_unit = serializers.ChoiceField(choices=ListingOption.Unit.choices)
    price = serializers.DecimalField(
        max_digits=9, decimal_places=2, required=False, allow_null=True,
        min_value=Decimal("1"), max_value=Decimal("9999999"),
    )
    min_hours = serializers.IntegerField(required=False, min_value=1, max_value=24)
    hours_per_acre = serializers.DecimalField(
        max_digits=3, decimal_places=1, required=False, allow_null=True,
        min_value=Decimal("0.1"), max_value=Decimal("20"),
    )

    def validate(self, attrs):
        machine: MachineType = self.context["machine_type"]

        # Attachment: none, a real implement, or अन्य + its name.
        implement = None
        implement_id = attrs.pop("implement_id", None)
        if implement_id is not None:
            implement = MachineType.objects.filter(pk=implement_id, is_active=True).first()
            if implement is None or (not implement.attachable and implement.code != OTHER):
                raise serializers.ValidationError({"implement_id": "not_an_implement"})
            if machine.kind == MachineType.Kind.IMPLEMENT:
                # An implement-only listing is the attachment itself: nothing more to attach.
                raise serializers.ValidationError({"implement_id": "implement_only_listing"})
            if not machine.takes_attachments:
                # Only tractors pull attachments (catalogue setting); a drone + rotavator makes no sense.
                raise serializers.ValidationError({"implement_id": "no_attachments"})
        attrs["implement"] = implement
        other_text = tidy(attrs.get("implement_other", ""))
        if implement is not None and implement.code == OTHER:
            if not other_text:
                raise serializers.ValidationError({"implement_other": "required"})
            attrs["implement_other"] = other_text
        else:
            attrs["implement_other"] = ""

        unit = attrs["price_unit"]
        priced = implement if implement is not None and implement.code != OTHER else machine
        if unit == ListingOption.Unit.ACRE and not priced.acre_price:
            # e.g. a trolley or a maize sheller: "per acre" makes no sense (catalogue setting).
            raise serializers.ValidationError({"price_unit": "no_acre"})
        if unit == ListingOption.Unit.NEGOTIABLE:
            attrs["price"] = None
        elif attrs.get("price") is None:
            raise serializers.ValidationError({"price": "required"})

        # Decision §22: the minimum is always 1 hour; the owner isn't asked (whatever is sent is ignored).
        attrs["min_hours"] = 1

        if unit == ListingOption.Unit.ACRE:
            own = attrs.get("hours_per_acre")
            default = (implement.default_hours_per_acre if implement else None) or machine.default_hours_per_acre
            if own is None and default is None:
                # e.g. tractor alone, trolley, अन्य: no starting value, so the owner must say (03 S14 step 4).
                raise serializers.ValidationError({"hours_per_acre": "required"})
            attrs["hours_per_acre"] = own
        else:
            if attrs.get("hours_per_acre") is not None:
                raise serializers.ValidationError({"hours_per_acre": "only_for_acre"})
            attrs["hours_per_acre"] = None
        return attrs


def option_key(o: dict | ListingOption) -> tuple:
    """Two options are 'the same' if attachment + unit match (the database rule)."""
    if isinstance(o, ListingOption):
        return (o.implement_id, o.implement_other, o.price_unit)
    return (o["implement"].pk if o["implement"] else None, o["implement_other"], o["price_unit"])


class OptionReadSerializer(serializers.ModelSerializer):
    implement = serializers.SerializerMethodField()
    hours_per_acre = serializers.SerializerMethodField()

    class Meta:
        model = ListingOption
        fields = ("id", "implement", "implement_other", "price_unit", "price", "min_hours", "hours_per_acre")

    def get_implement(self, o):
        if o.implement is None:
            return None
        return {"id": o.implement.id, "code": o.implement.code, "name_hi": o.implement.name_hi}

    def get_hours_per_acre(self, o):
        if o.price_unit != ListingOption.Unit.ACRE:
            return None
        value = o.effective_hours_per_acre()
        return str(value) if value is not None else None


# --------------------------------------------------------------------- photos


def listing_folder(firebase_uid: str) -> str:
    """Same folder as firebase/storage.rules: listings/{uid}/{draft or listing id}/…"""
    return f"listings/{firebase_uid}/"


class PhotoWriteSerializer(serializers.Serializer):
    """
    The phone uploaded two files to Firebase Storage itself; here it tells us where they are.
    Only files in the caller's own folder, in our bucket, as a matching _full / _thumb pair.
    """

    url = serializers.URLField(max_length=600)
    thumb_url = serializers.URLField(max_length=600)
    storage_path = serializers.CharField(max_length=300)

    def validate(self, attrs):
        uid = self.context["firebase_uid"]
        path = attrs["storage_path"]
        if not uid or not path.startswith(listing_folder(uid)) or not path.endswith("_full.jpg"):
            raise serializers.ValidationError({"storage_path": "not_own_photo"})
        if ".." in path.split("/") or path.count("/") != 3:
            raise serializers.ValidationError({"storage_path": "not_own_photo"})
        if bucket_path(attrs["url"]) != path:
            raise serializers.ValidationError({"url": "not_own_photo"})
        if bucket_path(attrs["thumb_url"]) != path.removesuffix("_full.jpg") + "_thumb.jpg":
            raise serializers.ValidationError({"thumb_url": "not_own_photo"})
        return attrs


class PhotoReadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Photo
        fields = ("id", "url", "thumb_url", "storage_path", "sort_order")


class PhotoOrderSerializer(serializers.Serializer):
    photo_ids = serializers.ListField(child=serializers.IntegerField(), max_length=Listing.MAX_PHOTOS)


# --------------------------------------------------------------------- location


class LocationSerializer(serializers.Serializer):
    """A pin picked on the map (S5) instead of a saved address."""

    lat = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal(Address.LAT_MIN), max_value=Decimal(Address.LAT_MAX)
    )
    lng = serializers.DecimalField(
        max_digits=9, decimal_places=6, min_value=Decimal(Address.LNG_MIN), max_value=Decimal(Address.LNG_MAX)
    )
    village = serializers.CharField(max_length=120, required=False, allow_blank=True)
    district = serializers.CharField(max_length=80, required=False, allow_blank=True)
    state = serializers.CharField(max_length=80, required=False, allow_blank=True)
    landmark = serializers.CharField(max_length=200, required=False, allow_blank=True)

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = {**data, **{f: round(float(data[f]), 6) for f in ("lat", "lng") if isinstance(data.get(f), (int, float))}}
        return super().to_internal_value(data)


def place_fields(*, user, address_id=None, location=None) -> dict:
    """The listing's own copy of a place: from one of my saved addresses, or a map pin."""
    if location is not None:
        return {
            "lat": location["lat"], "lng": location["lng"],
            **{f: tidy(location.get(f, "")) for f in ("village", "district", "state", "landmark")},
        }
    addr = None
    if address_id is not None:
        addr = Address.objects.filter(pk=address_id, user=user, is_deleted=False).first()
        if addr is None:
            raise serializers.ValidationError({"address_id": "not_found"})
    else:
        addr = user.home_address
        if addr is None or addr.is_deleted:
            raise serializers.ValidationError({"location": "required"})
    return {f: getattr(addr, f) for f in ("lat", "lng", "village", "district", "state", "landmark")}


# --------------------------------------------------------------------- listing


def max_year() -> int:
    return datetime.date.today().year + 1


def apply_settings(attrs: dict, machine: MachineType, creating: bool) -> None:
    """
    The terms are fixed now (owner, 2026-11-26, decision §22), not asked:
      - fuel is always included in the rent, where the machine uses fuel (a battery drone or an attachment
        pulled by the renter's own tractor: null = doesn't apply, no badge)
      - transport is always included, where it applies
      - the gap after each booking is always 1 hour
    Whatever the app sends for these is ignored. Only अन्य still answers "चलाने वाला साथ आएगा?".
    """
    if "operator_included" in attrs:
        attrs["operator_answer"] = attrs.pop("operator_included")
    for field in ("fuel_included", "transport_included", "gap_hours"):
        attrs.pop(field, None)
    if creating:
        attrs["fuel_included"] = True if machine.fuel not in (MachineType.Fuel.BATTERY, MachineType.Fuel.NONE) else None
        attrs["transport_included"] = True if machine.asks_transport else None
        attrs["gap_hours"] = Listing.GAP_HOURS
    if machine.operator != MachineType.Operator.ASK:
        attrs["operator_answer"] = None
    elif attrs.get("operator_answer") is None and ("operator_answer" in attrs or creating):
        attrs["operator_answer"] = False


def one_unit(options: list[dict]) -> None:
    """Decision §22: the owner picks the price unit ONCE; every price of a machine is in that unit."""
    if len({o["price_unit"] for o in options}) > 1:
        raise serializers.ValidationError({"options": "one_unit"})


def default_week() -> list[dict]:
    start, end = Listing.DEFAULT_HOURS
    return [{"weekday": d, "start_hour": start, "end_hour": end} for d in range(7)]


class ListingFieldsSerializer(serializers.Serializer):
    """Fields shared by create and PATCH. Everything optional here; create adds what's required."""

    machine_type_other = serializers.CharField(max_length=60, required=False, allow_blank=True)
    brand = serializers.CharField(max_length=60, required=False, allow_blank=True)
    model = serializers.CharField(max_length=60, required=False, allow_blank=True)
    hp = serializers.IntegerField(required=False, allow_null=True, min_value=1, max_value=200)
    year = serializers.IntegerField(required=False, allow_null=True, min_value=1960)
    min_tractor_hp = serializers.IntegerField(required=False, allow_null=True, min_value=10, max_value=200)
    description = serializers.CharField(max_length=500, required=False, allow_blank=True)
    address_id = serializers.IntegerField(required=False, allow_null=True)
    location = LocationSerializer(required=False, allow_null=True)
    # Fuel, transport and gap are fixed (decision §22) and not read; only अन्य answers the operator question.
    operator_included = serializers.BooleanField(required=False, allow_null=True)  # अन्य only

    def validate_year(self, value):
        if value is not None and value > max_year():
            raise serializers.ValidationError("in_future")
        return value

    def clean_common(self, attrs, machine: MachineType, creating: bool = False):
        for f in ("machine_type_other", "brand", "model", "description"):
            if f in attrs:
                attrs[f] = tidy(attrs[f])
        if machine.code == OTHER:
            if "machine_type_other" in attrs and not attrs["machine_type_other"]:
                raise serializers.ValidationError({"machine_type_other": "required"})
        else:
            attrs["machine_type_other"] = ""
        if machine.kind != MachineType.Kind.IMPLEMENT:
            attrs.pop("min_tractor_hp", None)
        apply_settings(attrs, machine, creating)
        return attrs


class ListingCreateSerializer(ListingFieldsSerializer):
    """POST /api/listings/: the listing, its options, and optionally photos and weekly hours, in one call."""

    machine_type_id = serializers.IntegerField()
    options = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=ListingOption.MAX_PER_LISTING)
    photos = PhotoWriteSerializer(many=True, required=False)
    weekly_hours = WeeklyHoursSerializer(many=True, required=False)

    def validate(self, attrs):
        machine = MachineType.objects.filter(pk=attrs.pop("machine_type_id"), is_active=True).first()
        if machine is None:
            raise serializers.ValidationError({"machine_type_id": "not_found"})
        attrs["machine_type"] = machine
        if machine.code == OTHER and not tidy(attrs.get("machine_type_other", "")):
            raise serializers.ValidationError({"machine_type_other": "required"})
        attrs = self.clean_common(attrs, machine, creating=True)

        attrs.update(place_fields(
            user=self.context["user"], address_id=attrs.pop("address_id", None), location=attrs.pop("location", None)
        ))

        # Options are checked against THIS listing's machine, so they're validated here, not as a nested field.
        options, errors = [], {}
        for i, raw in enumerate(attrs.pop("options")):
            ser = OptionWriteSerializer(data=raw, context={"machine_type": machine})
            if ser.is_valid():
                options.append(ser.validated_data)
            else:
                errors[str(i)] = ser.errors
        if errors:
            raise serializers.ValidationError({"options": errors})
        keys = [option_key(o) for o in options]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError({"options": "duplicate_option"})
        one_unit(options)
        attrs["options"] = options

        if len(attrs.get("photos", [])) > Listing.MAX_PHOTOS:
            raise serializers.ValidationError({"photos": "too_many_photos"})
        # Decision §22: no hours step in the wizard; a new machine is open every day 7–19 unless hours are sent.
        attrs["weekly_hours"] = validate_week(attrs["weekly_hours"]) if attrs.get("weekly_hours") else default_week()
        return attrs


class OptionsReplaceSerializer(serializers.Serializer):
    """PUT /api/listings/{id}/options/: the whole price list at once (S21 Edit), all in one unit."""

    options = serializers.ListField(child=serializers.DictField(), min_length=1, max_length=ListingOption.MAX_PER_LISTING)

    def validate(self, attrs):
        machine = self.context["machine_type"]
        options, errors = [], {}
        for i, raw in enumerate(attrs["options"]):
            ser = OptionWriteSerializer(data=raw, context={"machine_type": machine})
            if ser.is_valid():
                options.append(ser.validated_data)
            else:
                errors[str(i)] = ser.errors
        if errors:
            raise serializers.ValidationError({"options": errors})
        keys = [option_key(o) for o in options]
        if len(keys) != len(set(keys)):
            raise serializers.ValidationError({"options": "duplicate_option"})
        one_unit(options)
        return {"options": options}


class ListingUpdateSerializer(ListingFieldsSerializer):
    """PATCH /api/listings/{id}/. The machine type can't change: it would be a different machine."""

    status = serializers.ChoiceField(choices=[Listing.Status.ACTIVE, Listing.Status.PAUSED], required=False)

    def validate(self, attrs):
        listing: Listing = self.context["listing"]
        attrs = self.clean_common(attrs, listing.machine_type)
        if listing.machine_type.code != OTHER:
            attrs.pop("machine_type_other", None)
        address_id, location = attrs.pop("address_id", None), attrs.pop("location", None)
        if address_id is not None or location is not None:
            attrs.update(place_fields(user=self.context["user"], address_id=address_id, location=location))
        return attrs


def machine_json(m: MachineType) -> dict:
    # fuel: which word the fuel badge uses (डीज़ल / पेट्रोल / तेल), so S10 needs no catalogue lookup.
    return {"id": m.id, "code": m.code, "name_hi": m.name_hi, "kind": m.kind, "icon": m.icon, "fuel": m.fuel}


class ListingOwnerSerializer(serializers.ModelSerializer):
    """The full listing, as its owner sees it (after create, in S21 edit)."""

    machine = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    photos = PhotoReadSerializer(many=True, read_only=True)
    weekly_hours = serializers.SerializerMethodField()
    operator_included = serializers.BooleanField(read_only=True)
    bookable = serializers.BooleanField(read_only=True)

    class Meta:
        model = Listing
        fields = (
            "id", "machine", "machine_type_other", "brand", "model", "hp", "year", "min_tractor_hp", "description",
            "location", "fuel_included", "transport_included", "gap_hours", "operator_included",
            "has_schedule", "bookable", "status", "bookings_count", "options", "photos", "weekly_hours", "created_at",
        )

    def get_machine(self, obj):
        return machine_json(obj.machine_type)

    def get_location(self, obj):
        return {
            "lat": float(obj.lat), "lng": float(obj.lng), "village": obj.village, "district": obj.district,
            "state": obj.state, "landmark": obj.landmark,
        }

    def get_options(self, obj):
        active = [o for o in obj.options.all() if o.is_active]
        return OptionReadSerializer(active, many=True).data

    def get_weekly_hours(self, obj):
        return [
            {"weekday": w.weekday, "start_hour": w.start_hour, "end_hour": w.end_hour} for w in obj.weekly_hours.all()
        ]


class ListingMineSerializer(serializers.ModelSerializer):
    """A card in S16 My Machines."""

    machine = serializers.SerializerMethodField()
    cover_thumb_url = serializers.SerializerMethodField()
    options_count = serializers.SerializerMethodField()
    bookable = serializers.BooleanField(read_only=True)
    # Filled in by bookings (Phase 5); always there so the app's cards don't change shape later.
    next_booking = serializers.SerializerMethodField()
    pending_requests_count = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id", "machine", "machine_type_other", "brand", "model", "village", "status", "has_schedule",
            "bookable", "cover_thumb_url", "options_count", "bookings_count", "next_booking",
            "pending_requests_count",
        )

    def get_machine(self, obj):
        return machine_json(obj.machine_type)

    def get_cover_thumb_url(self, obj):
        photos = list(obj.photos.all())
        return photos[0].thumb_url if photos else None

    def get_options_count(self, obj):
        return sum(1 for o in obj.options.all() if o.is_active)

    def get_next_booking(self, obj):
        return None

    def get_pending_requests_count(self, obj):
        return 0


# ---- Search (renter side, docs/06-api.md §7) ----

class SearchParamsSerializer(serializers.Serializer):
    """Query string of GET /api/search/. Unknown job/machine codes are a 400, not an empty list,
    so an app bug shows up instead of looking like 'no machines nearby'."""

    lat = serializers.FloatField(min_value=-90, max_value=90)
    lng = serializers.FloatField(min_value=-180, max_value=180)
    radius_km = serializers.FloatField(min_value=0.5, max_value=100, default=10)
    job = serializers.SlugField(required=False)
    machine = serializers.SlugField(required=False)
    q = serializers.CharField(max_length=100, required=False, allow_blank=True, trim_whitespace=True)
    unit = serializers.ChoiceField(choices=ListingOption.Unit.values, required=False)
    hp_min = serializers.IntegerField(min_value=0, max_value=500, required=False)
    hp_max = serializers.IntegerField(min_value=0, max_value=500, required=False)
    date = serializers.DateField(required=False)  # "कब चाहिए?" (IST); today … today + 29

    def validate_date(self, day):
        from bookings.availability import window

        first, last = window()
        if not first <= day <= last:
            raise serializers.ValidationError("outside_30_days")
        return day

    def validate_job(self, code):
        if not JobCategory.objects.filter(code=code, is_active=True).exists():
            raise serializers.ValidationError("unknown")
        return code

    def validate_machine(self, code):
        if not MachineType.objects.filter(code=code).exists():
            raise serializers.ValidationError("unknown")
        return code

    def validate(self, attrs):
        if attrs.get("hp_min") is not None and attrs.get("hp_max") is not None and attrs["hp_min"] > attrs["hp_max"]:
            raise serializers.ValidationError({"hp_max": "below_hp_min"})
        return attrs


class SearchResultSerializer(serializers.ModelSerializer):
    """One search card. Privacy (06 §7): no phone, no landmark, no exact lat/lng, owner's first name only."""

    machine = serializers.SerializerMethodField()
    matched_implement = serializers.SerializerMethodField()
    from_price = serializers.SerializerMethodField()
    operator_included = serializers.BooleanField(read_only=True)
    bookable = serializers.BooleanField(read_only=True)
    distance_km = serializers.SerializerMethodField()
    thumb_url = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id", "machine", "machine_type_other", "brand", "model", "hp", "matched_implement", "from_price",
            "operator_included", "transport_included", "bookable", "village", "distance_km", "thumb_url", "owner",
        )

    @property
    def params(self):
        return self.context["params"]

    def get_machine(self, obj):
        return machine_json(obj.machine_type)

    def get_matched_implement(self, obj):
        m = matched_implement(obj, self.params)
        return {"code": m.code, "name_hi": m.name_hi, "icon": m.icon} if m else None

    def get_from_price(self, obj):
        return from_price(obj, self.params)

    def get_distance_km(self, obj):
        return round(obj.distance_km, 1)

    def get_thumb_url(self, obj):
        return obj.ordered_photos[0].thumb_url if obj.ordered_photos else None

    def get_owner(self, obj):
        first = (obj.owner.name or "").split()
        return {"first_name": first[0] if first else ""}


# ---- Details for renters (docs/06-api.md §7) ----

class ListingPublicSerializer(serializers.ModelSerializer):
    """S10 details for anyone who isn't the owner. Privacy (06 §7): no phone, landmark, exact lat/lng,
    district/state, storage paths or owner id; the owner's first name, village and month joined only."""

    machine = serializers.SerializerMethodField()
    photos = serializers.SerializerMethodField()
    options = serializers.SerializerMethodField()
    weekly_hours = serializers.SerializerMethodField()
    operator_included = serializers.BooleanField(read_only=True)
    bookable = serializers.BooleanField(read_only=True)
    distance_km = serializers.SerializerMethodField()
    owner = serializers.SerializerMethodField()

    class Meta:
        model = Listing
        fields = (
            "id", "machine", "machine_type_other", "brand", "model", "hp", "year", "min_tractor_hp", "description",
            "photos", "options", "operator_included", "fuel_included", "transport_included", "gap_hours",
            "bookable", "weekly_hours", "village", "distance_km", "owner", "status",
        )

    def get_machine(self, obj):
        return machine_json(obj.machine_type)

    def get_photos(self, obj):
        return [{"url": p.url, "thumb_url": p.thumb_url} for p in obj.photos.all()]

    def get_options(self, obj):
        return OptionReadSerializer([o for o in obj.options.all() if o.is_active], many=True).data

    def get_weekly_hours(self, obj):
        return [
            {"weekday": w.weekday, "start_hour": w.start_hour, "end_hour": w.end_hour} for w in obj.weekly_hours.all()
        ]

    def get_distance_km(self, obj):
        point = self.context.get("from_point")
        return round(haversine_km(point, (float(obj.lat), float(obj.lng))), 1) if point else None

    def get_owner(self, obj):
        user = obj.owner
        words = (user.name or "").split()
        home = user.home_address
        return {
            "first_name": words[0] if words else "",
            "village": home.village if home else "",
            "member_since": user.date_joined.strftime("%Y-%m"),
        }


class FromPointSerializer(serializers.Serializer):
    """Optional ?lat&lng on the details: both or neither."""

    lat = serializers.FloatField(min_value=-90, max_value=90, required=False)
    lng = serializers.FloatField(min_value=-180, max_value=180, required=False)

    def validate(self, attrs):
        if ("lat" in attrs) != ("lng" in attrs):
            raise serializers.ValidationError({"lng" if "lat" in attrs else "lat": "required"})
        return attrs
