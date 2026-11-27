"""
Decision §23 (owner, 2026-11-27), BUGS.md #19: every machine is open every day सुबह 7 बजे से शाम 7 बजे तक.

Decision §22 only gave 7–19 to machines WITHOUT hours. Machines whose owner had set hours before kept them, e.g. a
tractor at 0–24 ("हर दिन रात 12 बजे से रात 12 बजे तक", start times until रात 11 बजे), others at 6–18 or 6–12.
This puts every machine (removed ones too, so nothing comes back wrong) on every day 7–19, and drops day changes
that gave other HOURS. Closed days are kept: closing a day stays possible (slice 8).

Bookings already made keep their own times. Not undoable (the old hours are not kept), so the reverse does nothing.
"""

from django.db import migrations

START, END = 7, 19


def fix_hours(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    WeeklyHours = apps.get_model("listings", "WeeklyHours")
    DayOverride = apps.get_model("listings", "DayOverride")

    WeeklyHours.objects.all().delete()
    ids = list(Listing.objects.values_list("pk", flat=True))
    WeeklyHours.objects.bulk_create(
        WeeklyHours(listing_id=pk, weekday=d, start_hour=START, end_hour=END) for pk in ids for d in range(7)
    )
    Listing.objects.update(has_schedule=True)
    dropped = DayOverride.objects.filter(is_closed=False).delete()[0]
    if ids:
        print(f"\n  every day 7–19 for {len(ids)} machines; {dropped} day changes with other hours dropped")


class Migration(migrations.Migration):
    dependencies = [("listings", "0005_simple_terms")]

    operations = [migrations.RunPython(fix_hours, migrations.RunPython.noop)]
