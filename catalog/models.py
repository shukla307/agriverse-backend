"""
The fixed lists the whole app is built on (docs/05-data-model.md §3.3–3.4):
- JobCategory: the work a farmer wants done (जुताई, बुवाई, …). The Home grid.
- MachineType: the machines and attachments that do it (ट्रैक्टर, रोटावेटर, …).

Both are managed by admin. The app keeps a copy on the phone (GET /api/machine-types/)
for the Home grid and offline search suggestions.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models


class JobCategory(models.Model):
    code = models.SlugField(max_length=40, unique=True)
    name_hi = models.CharField(max_length=60)
    name_en = models.CharField(max_length=60, blank=True)
    icon = models.CharField(max_length=60, blank=True, help_text="Icon name the app knows, e.g. ic_land_prep")
    sort_order = models.PositiveSmallIntegerField(default=50, help_text="Home grid order; 'other' is 99 (always last)")
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "id")
        verbose_name_plural = "job categories"

    def __str__(self):
        return f"{self.name_hi} ({self.code})"


class MachineType(models.Model):
    class Kind(models.TextChoices):
        POWER = "power", "Power machine (has its own engine/motor)"
        IMPLEMENT = "implement", "Implement (attachment, needs a tractor)"

    class Fuel(models.TextChoices):
        """Which fuel question the wizard asks, and which badge S10 shows (owner, 2026-11-23)."""
        DIESEL = "diesel", "Diesel → डीज़ल शामिल?"
        PETROL = "petrol", "Petrol → पेट्रोल शामिल?"
        DIESEL_OR_PETROL = "diesel_or_petrol", "Diesel or petrol → तेल (डीज़ल / पेट्रोल) शामिल?"
        BATTERY = "battery", "Battery (no question)"
        NONE = "none", "No fuel (attachment: the renter's tractor) — no question"
        ASK = "ask", "Owner says (अन्य): yes / no / तेल नहीं लगता"

    class Operator(models.TextChoices):
        ALWAYS = "always", "Always comes with the owner's operator"
        NEVER = "never", "Never (attachment listed alone)"
        ASK = "ask", "Owner says (अन्य)"

    code = models.SlugField(max_length=40, unique=True)
    name_hi = models.CharField(max_length=80)
    name_en = models.CharField(max_length=80, blank=True)
    # Other spellings that must find this type (glossary §7). Stored lower-case.
    search_aliases = ArrayField(models.CharField(max_length=60), default=list, blank=True)
    icon = models.CharField(max_length=60, blank=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, null=True, blank=True,
                            help_text="Empty only for 'other' (अन्य)")
    jobs = models.ManyToManyField(JobCategory, related_name="machine_types", blank=True)
    default_hours_per_acre = models.DecimalField(max_digits=3, decimal_places=1, null=True, blank=True)

    # What the "list a machine" wizard asks and what S10 shows for THIS machine (owner, 2026-11-23).
    fuel = models.CharField(max_length=20, choices=Fuel.choices, default=Fuel.NONE)
    operator = models.CharField(max_length=10, choices=Operator.choices, default=Operator.NEVER)
    asks_transport = models.BooleanField(default=False, help_text="Ask 'transport included / extra'")
    attachable = models.BooleanField(default=False, help_text="Can be added to a tractor listing as an attachment")
    takes_attachments = models.BooleanField(default=False, help_text="Owner can add attachments (tractors)")
    has_brand = models.BooleanField(default=False, help_text="Ask brand (tractor brand list) + HP")
    acre_price = models.BooleanField(default=True, help_text="Can be rented per acre (एकड़)")

    sort_order = models.PositiveSmallIntegerField(default=50)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ("sort_order", "id")

    def __str__(self):
        return f"{self.name_hi} ({self.code})"

    def save(self, *args, **kwargs):
        # One rule, in one place: aliases are compared lower-case, without extra spaces.
        cleaned = []
        for alias in self.search_aliases or []:
            a = " ".join(str(alias).split()).lower()
            if a and a not in cleaned:
                cleaned.append(a)
        self.search_aliases = cleaned
        super().save(*args, **kwargs)
