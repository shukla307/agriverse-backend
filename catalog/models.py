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
        POWER = "power", "Power machine (moves on its own, comes with operator)"
        IMPLEMENT = "implement", "Implement (attachment, needs a tractor)"

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
