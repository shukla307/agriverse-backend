"""
A machine an owner rents out (docs/05-data-model.md §3.5–3.8).

  Listing        one physical machine (a tractor, a harvester, a rotavator on its own…)
  ListingOption  one way to book it at one price ("tractor + rotavator, ₹1,000 / घंटा")
  Photo          0–5 photos; the first is the cover (decision §17)
  WeeklyHours    when it's free each week; without these the listing is NOT bookable (decision §11)
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from catalog.models import MachineType


class Listing(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "दिख रही है"
        PAUSED = "paused", "रुकी हुई"
        REMOVED = "removed", "हटाई गई"

    GAP_CHOICES = [(0, "No gap"), (1, "1 hour"), (2, "2 hours")]
    # Owner, 2026-11-26 (decision §22): the wizard asks none of these any more; every listing gets them.
    GAP_HOURS = 1                      # always 1 hour after each booking
    DEFAULT_HOURS = (7, 19)            # every day, सुबह 7 बजे से शाम 7 बजे तक, so a new machine is bookable at once
    MAX_PHOTOS = 5

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="listings")
    machine_type = models.ForeignKey(MachineType, on_delete=models.PROTECT, related_name="listings")
    machine_type_other = models.CharField(max_length=60, blank=True, help_text="Typed name when the type is अन्य")
    brand = models.CharField(max_length=60, blank=True)
    model = models.CharField(max_length=60, blank=True)
    hp = models.PositiveSmallIntegerField(null=True, blank=True)
    year = models.PositiveSmallIntegerField(null=True, blank=True)
    min_tractor_hp = models.PositiveSmallIntegerField(
        null=True, blank=True, help_text="Only for implement-only listings (renter's tractor needed)"
    )
    description = models.TextField(blank=True)

    # The machine's own place: copied from an address or picked on the map. Not kept in sync with the profile.
    lat = models.DecimalField(max_digits=9, decimal_places=6)
    lng = models.DecimalField(max_digits=9, decimal_places=6)
    village = models.CharField(max_length=120, blank=True)
    district = models.CharField(max_length=80, blank=True)
    state = models.CharField(max_length=80, blank=True)
    landmark = models.CharField(max_length=200, blank=True, help_text="Private until a booking is accepted")

    # Each one is asked only if this machine type asks it (catalog MachineType settings, owner 2026-11-23).
    # null = "doesn't apply" (a battery drone, an attachment pulled by the renter's own tractor…): no badge.
    fuel_included = models.BooleanField(null=True, blank=True)
    # Transport to the field is the owner's job; this only says included or charged extra.
    transport_included = models.BooleanField(null=True, blank=True)
    # Only for अन्य (operator = "ask"): the owner's answer to "चलाने वाला साथ आएगा?".
    operator_answer = models.BooleanField(null=True, blank=True)
    gap_hours = models.PositiveSmallIntegerField(choices=GAP_CHOICES, default=1)
    has_schedule = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.ACTIVE)
    bookings_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["status", "lat", "lng"]),
            models.Index(fields=["machine_type", "status"]),
            models.Index(fields=["owner", "status"]),
        ]
        constraints = [
            models.CheckConstraint(check=Q(gap_hours__in=[0, 1, 2]), name="listing_gap_0_1_2"),
        ]

    def __str__(self):
        name = self.machine_type_other or self.machine_type.name_hi
        return f"#{self.pk} {name} — {self.owner}"

    @property
    def operator_included(self) -> bool:
        """From the machine type (power machines: always, decision §3); for अन्य, the owner's answer."""
        op = self.machine_type.operator
        if op == MachineType.Operator.ASK:
            return bool(self.operator_answer)
        return op == MachineType.Operator.ALWAYS

    @property
    def bookable(self) -> bool:
        return self.status == self.Status.ACTIVE and self.has_schedule


class ListingOption(models.Model):
    class Unit(models.TextChoices):
        HOUR = "hour", "घंटा"
        DAY = "day", "दिन"
        ACRE = "acre", "एकड़"
        NEGOTIABLE = "negotiable", "मोल-भाव"

    MAX_PER_LISTING = 20

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="options")
    # null = "machine only" (the tractor alone, or the drone itself).
    implement = models.ForeignKey(
        MachineType, null=True, blank=True, on_delete=models.PROTECT, related_name="as_implement_in"
    )
    implement_other = models.CharField(max_length=60, blank=True)
    price_unit = models.CharField(max_length=12, choices=Unit.choices)
    price = models.DecimalField(max_digits=9, decimal_places=2, null=True, blank=True)
    min_hours = models.PositiveSmallIntegerField(default=1)
    # Owner's own value. null = use the implement's default, then the machine's (05 §3.4a).
    hours_per_acre = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("id",)
        constraints = [
            # One active option per (attachment, unit). Two partial constraints, because
            # in PostgreSQL two NULL implements never count as "the same".
            models.UniqueConstraint(
                fields=["listing", "implement", "implement_other", "price_unit"],
                condition=Q(is_active=True, implement__isnull=False),
                name="option_unique_with_implement",
            ),
            models.UniqueConstraint(
                fields=["listing", "price_unit"],
                condition=Q(is_active=True, implement__isnull=True),
                name="option_unique_machine_only",
            ),
            models.CheckConstraint(
                check=Q(price_unit="negotiable") | Q(price__isnull=False), name="option_price_unless_negotiable"
            ),
        ]

    def __str__(self):
        what = self.implement_other or (self.implement.name_hi if self.implement else "सिर्फ़ मशीन")
        return f"{what}: {self.price or 'मोल-भाव'} / {self.get_price_unit_display()}"

    def effective_hours_per_acre(self):
        """Owner's value, else the attachment's default, else the machine's (05 §3.4a)."""
        if self.hours_per_acre is not None:
            return self.hours_per_acre
        if self.implement and self.implement.default_hours_per_acre is not None:
            return self.implement.default_hours_per_acre
        return self.listing.machine_type.default_hours_per_acre


class Photo(models.Model):
    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="photos")
    url = models.URLField(max_length=600)
    thumb_url = models.URLField(max_length=600)
    storage_path = models.CharField(max_length=300)
    sort_order = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("sort_order", "id")

    def __str__(self):
        return self.storage_path


class WeeklyHours(models.Model):
    """One open weekday. 0 = सोम … 6 = रवि. Whole IST hours; 24 = midnight. No crossing midnight."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="weekly_hours")
    weekday = models.PositiveSmallIntegerField()
    start_hour = models.PositiveSmallIntegerField()
    end_hour = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ("weekday",)
        verbose_name_plural = "weekly hours"
        constraints = [
            models.UniqueConstraint(fields=["listing", "weekday"], name="weekly_one_row_per_day"),
            models.CheckConstraint(check=Q(weekday__lte=6), name="weekly_weekday_0_6"),
            models.CheckConstraint(check=Q(end_hour__gt=models.F("start_hour")), name="weekly_end_after_start"),
            models.CheckConstraint(check=Q(end_hour__lte=24), name="weekly_end_max_24"),
        ]

    def __str__(self):
        return f"{self.weekday}: {self.start_hour}–{self.end_hour}"


class DayOverride(models.Model):
    """One date that differs from the weekly schedule (decision §11 step 2, 05 §3.8): closed all day, or
    different hours. Deleting the row = the day goes back to its weekly hours."""

    listing = models.ForeignKey(Listing, on_delete=models.CASCADE, related_name="day_overrides")
    date = models.DateField(help_text="IST date")
    is_closed = models.BooleanField(default=False)
    start_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    end_hour = models.PositiveSmallIntegerField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("date",)
        constraints = [
            models.UniqueConstraint(fields=["listing", "date"], name="override_one_per_date"),
            # Closed → no hours. Open → both hours, "तक" after "से", at most midnight.
            models.CheckConstraint(
                check=(Q(is_closed=True, start_hour__isnull=True, end_hour__isnull=True)
                       | Q(is_closed=False, start_hour__isnull=False, end_hour__gt=models.F("start_hour"),
                           end_hour__lte=24)),
                name="override_closed_or_hours",
            ),
        ]

    def __str__(self):
        return f"#{self.listing_id} {self.date}: " + ("बंद" if self.is_closed else f"{self.start_hour}–{self.end_hour}")


class NewTypeRequest(Listing):
    """
    Admin-only view of listings where the owner picked "अन्य" (as the machine or an attachment).
    Admin adds the proper MachineType, then moves these listings to it (05 §3.4).
    """

    class Meta:
        proxy = True
        verbose_name = "new machine type to add"
        verbose_name_plural = "new machine types to add"
