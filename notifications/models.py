"""
In-app notifications (docs/05-data-model.md §3.11, Phase 5 slice 6). The bell's list (S19) is the source of
truth; the pushes (slice 7) only announce rows that already exist here (07 §4: a failed push loses nothing).

The server stores the TYPE + a few PARAMS (first names, machine, times), never Hindi sentences: the app writes
the words from its ARB file (glossary §8), so a word change never needs a migration.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from bookings.models import Booking


class Notification(models.Model):
    class Type(models.TextChoices):
        # glossary §8. Who gets which: notify.WHO.
        BOOKING_REQUESTED = "booking_requested"              # → owner
        BOOKING_ACCEPTED = "booking_accepted"                # → renter
        BOOKING_REJECTED = "booking_rejected"                # → renter
        BOOKING_EXPIRED = "booking_expired"                  # → renter ("मालिक ने समय पर जवाब नहीं दिया…")
        BOOKING_EXPIRED_OWNER = "booking_expired_owner"      # → owner ("आपने समय पर जवाब नहीं दिया…")
        BOOKING_CANCELLED_BY_RENTER = "booking_cancelled_by_renter"  # → owner
        BOOKING_CANCELLED_BY_OWNER = "booking_cancelled_by_owner"    # → renter
        BOOKING_COMPLETED = "booking_completed"              # → the other side (both when the system completes)

    class Role(models.TextChoices):
        OWNER = "owner"    # a tap opens S17
        RENTER = "renter"  # a tap opens S18

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="notifications")
    booking = models.ForeignKey(Booking, null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    type = models.CharField(max_length=40, choices=Type.choices)
    role = models.CharField(max_length=10, choices=Role.choices, help_text="what the user is in this booking")
    params = models.JSONField(default=dict, help_text="snapshot: renter, owner, machine, implement, start, end")
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        indexes = [
            models.Index(fields=["user", "-created_at", "-id"]),
            # The bell's count: only the unread rows, which stay few.
            models.Index(fields=["user"], condition=Q(read_at__isnull=True), name="notification_unread"),
        ]

    def __str__(self):
        return f"#{self.pk} {self.type} → user #{self.user_id}"
