from rest_framework import serializers

from .models import User


class UserSerializer(serializers.ModelSerializer):
    """What the app may know about the logged-in user (docs/06-api.md §3).
    home_address / addresses are added with the location screens (S5/S6)."""

    class Meta:
        model = User
        fields = ("id", "phone", "name", "photo_url")
        read_only_fields = fields


class FirebaseLoginSerializer(serializers.Serializer):
    firebase_id_token = serializers.CharField(max_length=4096, trim_whitespace=True)


class RefreshOnlySerializer(serializers.Serializer):
    refresh = serializers.CharField(max_length=2048)
