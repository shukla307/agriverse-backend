from rest_framework import serializers
from .models import Equipment

class EquipmentSerializer(serializers.ModelSerializer):
    owner_username = serializers.CharField(
        source="owner.username",
        read_only=True
    )

    class Meta:
        model = Equipment
        fields = [
            "id",
            "equipment_name",
            "price_per_day",
            "description",
            "contact_number",
            "location",
            "latitude",
            "longitude",
            "image_url",
            "available",
            "owner_username",
            "created_at",
        ]
        read_only_fields = ("latitude", "longitude")

