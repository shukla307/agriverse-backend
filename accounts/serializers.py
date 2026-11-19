from urllib.parse import unquote, urlsplit

from django.conf import settings
from rest_framework import serializers

from decimal import Decimal

from .models import Address, User


class AddressSerializer(serializers.ModelSerializer):
    """
    POST /api/me/addresses/ and the address lists (docs/06-api.md §3).
    Only India; "other" needs a name; village may be blank (slow internet on the phone).
    """

    lat = serializers.DecimalField(
        max_digits=9, decimal_places=6, coerce_to_string=False,
        min_value=Decimal(Address.LAT_MIN), max_value=Decimal(Address.LAT_MAX),
    )
    lng = serializers.DecimalField(
        max_digits=9, decimal_places=6, coerce_to_string=False,
        min_value=Decimal(Address.LNG_MIN), max_value=Decimal(Address.LNG_MAX),
    )
    make_home = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = Address
        fields = ("id", "label", "label_other", "lat", "lng", "village", "district", "state", "landmark", "make_home")
        read_only_fields = ("id",)

    def to_internal_value(self, data):
        # The phone sends more decimals than we keep (~10 cm is plenty).
        if hasattr(data, "copy"):
            data = data.copy()
            for f in ("lat", "lng"):
                v = data.get(f)
                if isinstance(v, (int, float)):
                    data[f] = round(float(v), 6)
        return super().to_internal_value(data)

    def validate(self, attrs):
        for f in ("label_other", "village", "district", "state", "landmark"):
            if f in attrs:
                attrs[f] = " ".join(attrs[f].split())
        if attrs.get("label") == Address.Label.OTHER and not attrs.get("label_other"):
            raise serializers.ValidationError({"label_other": "required"})
        if attrs.get("label") != Address.Label.OTHER:
            attrs["label_other"] = ""
        return attrs


class UserSerializer(serializers.ModelSerializer):
    """What the app may know about the logged-in user (docs/06-api.md §3)."""

    home_address = AddressSerializer(read_only=True)

    class Meta:
        model = User
        fields = ("id", "phone", "name", "photo_url", "home_address")
        read_only_fields = fields


class FirebaseLoginSerializer(serializers.Serializer):
    firebase_id_token = serializers.CharField(max_length=4096, trim_whitespace=True)


class RefreshOnlySerializer(serializers.Serializer):
    refresh = serializers.CharField(max_length=2048)


def profile_photo_path(firebase_uid: str) -> str:
    """The one place a user's profile photo may live (same path as firebase/storage.rules)."""
    return f"profiles/{firebase_uid}/avatar.jpg"


def is_own_profile_photo(url: str, firebase_uid: str | None) -> bool:
    """
    True only for a Firebase Storage download link to THIS user's avatar in OUR bucket:
    https://firebasestorage.googleapis.com/v0/b/<bucket>/o/profiles%2F<uid>%2Favatar.jpg?alt=media&token=…
    Stops someone saving a link to another person's photo or to any other website.
    """
    if not firebase_uid:
        return False
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "firebasestorage.googleapis.com":
        return False
    prefix = f"/v0/b/{settings.FIREBASE_STORAGE_BUCKET}/o/"
    if not parts.path.startswith(prefix):
        return False
    return unquote(parts.path[len(prefix):]) == profile_photo_path(firebase_uid)


class MeUpdateSerializer(serializers.Serializer):
    """PATCH /api/me/ (S4 now; S20 Profile later). Both fields optional."""

    name = serializers.CharField(min_length=2, max_length=80, trim_whitespace=True, required=False)
    # "" removes the photo.
    photo_url = serializers.CharField(max_length=500, allow_blank=True, trim_whitespace=True, required=False)

    def validate_name(self, value: str) -> str:
        # "Ramesh    Kumar" → "Ramesh Kumar"; inner newlines/tabs become one space.
        value = " ".join(value.split())
        if len(value) < 2:
            raise serializers.ValidationError("too_short")
        return value

    def validate_photo_url(self, value: str) -> str:
        if value and not is_own_profile_photo(value, self.context["user"].firebase_uid):
            raise serializers.ValidationError("not_own_photo")
        return value
