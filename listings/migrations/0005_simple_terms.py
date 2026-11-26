"""
Decision §22 (owner, 2026-11-26): the wizard no longer asks these, so every existing listing gets the fixed values.

  - gap after each booking: 1 hour
  - fuel included (where the machine uses fuel), transport included (where it applies)
  - minimum hours: 1
  - one price unit per machine: a machine with prices in several units keeps the unit with the MOST prices
    (tie: hour → acre → day → मोल-भाव); prices in other units are switched OFF, never deleted, so bookings that
    point at them keep working
  - a machine without hours gets every day 7–19, so it's bookable at once

Bookings already made keep their own blocked_until (gap) and price snapshot. Not undoable (the old answers are
not kept), so the reverse does nothing.
"""

from collections import Counter

from django.db import migrations

UNIT_ORDER = ["hour", "acre", "day", "negotiable"]
DEFAULT_START, DEFAULT_END = 7, 19


def simplify(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    ListingOption = apps.get_model("listings", "ListingOption")
    WeeklyHours = apps.get_model("listings", "WeeklyHours")

    switched_off = given_hours = 0
    for listing in Listing.objects.select_related("machine_type").iterator():
        m = listing.machine_type
        listing.gap_hours = 1
        listing.fuel_included = True if m.fuel not in ("battery", "none") else None
        listing.transport_included = True if m.asks_transport else None

        active = list(ListingOption.objects.filter(listing=listing, is_active=True))
        units = Counter(o.price_unit for o in active)
        if len(units) > 1:
            keep = max(units, key=lambda u: (units[u], -UNIT_ORDER.index(u)))
            drop = [o.pk for o in active if o.price_unit != keep]
            ListingOption.objects.filter(pk__in=drop).update(is_active=False)
            switched_off += len(drop)

        if not listing.has_schedule and listing.status != "removed":
            WeeklyHours.objects.filter(listing=listing).delete()
            WeeklyHours.objects.bulk_create(
                WeeklyHours(listing=listing, weekday=d, start_hour=DEFAULT_START, end_hour=DEFAULT_END) for d in range(7)
            )
            listing.has_schedule = True
            given_hours += 1
        listing.save(update_fields=["gap_hours", "fuel_included", "transport_included", "has_schedule"])

    ListingOption.objects.exclude(min_hours=1).update(min_hours=1)
    if switched_off or given_hours:
        print(f"\n  decision §22: {switched_off} prices in a second unit switched off, "
              f"{given_hours} machines given hours 7–19")


class Migration(migrations.Migration):
    dependencies = [("listings", "0004_day_override")]

    operations = [migrations.RunPython(simplify, migrations.RunPython.noop)]
