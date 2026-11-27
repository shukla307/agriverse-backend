"""
Bookings (docs/05-data-model.md §3.9–3.10, §5).

  Booking       one renter's request for one machine at one time. What it reserves is the LISTING (the real
                machine), from start_at until blocked_until (= work time + the owner's gap).
  BookingEvent  history of every status change, never edited.

Double booking is stopped in 3 layers (05 §5): the app shows only free start times, the create view checks
under a lock (slice 3), and the database itself refuses two active bookings whose reserved ranges overlap
(the exclusion constraint below, PostgreSQL only).
"""

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import F, Func, Q

from catalog.models import JobCategory
from listings.models import Listing, ListingOption


class TsTzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Booking(models.Model):
    class Status(models.TextChoices):
        REQUESTED = "requested", "भेजी गई"
        ACCEPTED = "accepted", "पक्की"
        REJECTED = "rejected", "मना हो गई"
        EXPIRED = "expired", "समय निकल गया"
        CANCELLED_BY_RENTER = "cancelled_by_renter", "किसान ने रद्द की"
        CANCELLED_BY_OWNER = "cancelled_by_owner", "मालिक ने रद्द की"
        COMPLETED = "completed", "काम पूरा"

    class Reason(models.TextChoices):
        BUSY = "busy", "उस दिन खाली नहीं"
        REPAIR = "repair", "मशीन ख़राब है"
        TOO_FAR = "too_far", "बहुत दूर है"
        # The renter's own (slice 5): the owner's chips above don't fit a renter.
        NOT_NEEDED = "not_needed", "अब ज़रूरत नहीं है"
        FOUND_OTHER = "found_other", "दूसरी मशीन मिल गई"
        WEATHER = "weather", "मौसम ख़राब है"
        OTHER = "other", "कोई और वजह"

    # These hold the machine. A `requested` row past its expires_at still counts for the database until it's
    # marked expired, so every write marks stale rows expired first (05 §5); every read ignores them.
    ACTIVE = (Status.REQUESTED, Status.ACCEPTED)

    listing = models.ForeignKey(Listing, on_delete=models.PROTECT, related_name="bookings")
    option = models.ForeignKey(ListingOption, on_delete=models.PROTECT, related_name="bookings")
    renter = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings")
    job = models.ForeignKey(JobCategory, on_delete=models.PROTECT, related_name="bookings")
    job_other = models.CharField(max_length=60, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.REQUESTED)

    # Always whole IST hours, stored in UTC. [start_at, end_at) = the work; [start_at, blocked_until) = reserved.
    start_at = models.DateTimeField()
    end_at = models.DateTimeField()
    blocked_until = models.DateTimeField(help_text="end_at + the listing's gap, copied at request time")
    # Several days (per-day unit) = one row per day, sharing a group_id; actions apply to the whole group.
    group_id = models.UUIDField(null=True, blank=True)

    quantity = models.DecimalField(max_digits=5, decimal_places=1, help_text="hours, acres (0.5 steps) or days")
    hours = models.PositiveSmallIntegerField(help_text="whole hours reserved (this day)")
    # Snapshots, so a later price edit doesn't change a booking.
    price_unit = models.CharField(max_length=12, choices=ListingOption.Unit.choices)
    unit_price = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    hours_per_acre_used = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    estimated_total = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)

    # Where the work happens. The landmark and pin reach the owner only after acceptance.
    field_lat = models.DecimalField(max_digits=9, decimal_places=6)
    field_lng = models.DecimalField(max_digits=9, decimal_places=6)
    field_village = models.CharField(max_length=120, blank=True)
    field_landmark = models.CharField(max_length=200, blank=True)

    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    reason = models.CharField(max_length=12, choices=Reason.choices, blank=True)
    reason_other = models.CharField(max_length=60, blank=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["listing", "start_at"]),
            models.Index(fields=["renter", "status", "start_at"]),
            models.Index(fields=["status", "expires_at"]),
            models.Index(fields=["group_id"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(end_at__gt=F("start_at")), name="booking_end_after_start"),
            models.CheckConstraint(check=Q(blocked_until__gte=F("end_at")), name="booking_blocked_after_end"),
            # Layer 3 (05 §5): never two active bookings on one machine whose reserved ranges overlap.
            # "[)" = gap 0 lets 8–11 and 11–13 both exist; with gap 1 the first reserves 8–12.
            ExclusionConstraint(
                name="no_overlapping_active_bookings",
                expressions=[
                    ("listing", RangeOperators.EQUAL),
                    (TsTzRange("start_at", "blocked_until", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(status__in=["requested", "accepted"]),
            ),
        ]

    def __str__(self):
        return f"#{self.pk} {self.get_status_display()} — listing #{self.listing_id}, {self.start_at:%Y-%m-%d %H:%M}"


class BookingEvent(models.Model):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=20, choices=Booking.Status.choices, blank=True)
    to_status = models.CharField(max_length=20, choices=Booking.Status.choices)
    actor = models.ForeignKey(  # null = the system (expiry, auto-complete)
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reason = models.CharField(max_length=12, choices=Booking.Reason.choices, blank=True)
    reason_other = models.CharField(max_length=60, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("created_at", "id")

    def __str__(self):
        return f"#{self.booking_id}: {self.from_status or '—'} → {self.to_status}"
