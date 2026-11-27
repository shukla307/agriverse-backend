"""
The bell (docs/06-api.md §9, Phase 5 slice 6).

  GET  /api/notifications/?page=N        S19: newest first, 20 per page
  GET  /api/notifications/unread-count/  S7 bell badge: {"count": 2}
  POST /api/notifications/read/          {"ids": [..]} or {"all": true} → 204 (S19 marks what it showed)

Both reads first run the lazy expiry on my bookings (05 §5), so "time ran out" is in the bell as soon as it's
true, even before anyone opens a booking list (the Render cron job comes later).
"""

from django.db.models import Q
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from bookings.actions import expire_overdue
from bookings.models import Booking
from bookings.serializers import ist
from core.errors import ApiError
from core.pagination import Pages

from .models import Notification


def catch_up(user):
    expire_overdue(Booking.objects.filter(Q(renter=user) | Q(listing__owner=user)), timezone.now())


def notification_json(n: Notification) -> dict:
    return {
        "id": n.pk,
        "type": n.type,
        "role": n.role,
        "booking_id": n.booking_id,
        "params": n.params,
        "read_at": ist(n.read_at),
        "created_at": ist(n.created_at),
    }


class NotificationsView(APIView):
    def get(self, request):
        catch_up(request.user)
        pages = Pages()
        page = pages.paginate_queryset(Notification.objects.filter(user=request.user), request, view=self)
        return pages.get_paginated_response([notification_json(n) for n in page])


class UnreadCountView(APIView):
    def get(self, request):
        catch_up(request.user)
        return Response({"count": Notification.objects.filter(user=request.user, read_at__isnull=True).count()})


class ReadSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.IntegerField(), required=False, max_length=200)
    all = serializers.BooleanField(required=False, default=False)

    def validate(self, attrs):
        if not attrs["all"] and not attrs.get("ids"):
            raise ApiError("validation_error", fields={"ids": "required"})
        return attrs


class ReadView(APIView):
    def post(self, request):
        ser = ReadSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        qs = Notification.objects.filter(user=request.user, read_at__isnull=True)  # only my own, ever
        if not ser.validated_data["all"]:
            qs = qs.filter(pk__in=ser.validated_data["ids"])
        qs.update(read_at=timezone.now())
        return Response(status=status.HTTP_204_NO_CONTENT)
