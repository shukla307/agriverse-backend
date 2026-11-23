"""
Per-machine settings (owner, 2026-11-23):
- diesel_included → fuel_included (RENAMED, so the owners' answers are kept), and it may be empty.
- transport_included may be empty; operator_answer is new (अन्य only).
- Then the old answers that don't apply to the machine are emptied (a drone's "diesel", an attachment
  pulled by the renter's tractor…), and थ्रेशर listings drop min_tractor_hp (now a power machine).
"""

from django.db import migrations, models


def clean(apps, schema_editor):
    Listing = apps.get_model("listings", "Listing")
    Listing.objects.filter(machine_type__fuel__in=["battery", "none"]).update(fuel_included=None)
    Listing.objects.filter(machine_type__asks_transport=False).update(transport_included=None)
    Listing.objects.exclude(machine_type__operator="ask").update(operator_answer=None)
    Listing.objects.exclude(machine_type__kind="implement").update(min_tractor_hp=None)


class Migration(migrations.Migration):
    dependencies = [
        ("catalog", "0004_machine_settings_data"),
        ("listings", "0002_pg_trgm"),
    ]

    operations = [
        migrations.RenameField("listing", "diesel_included", "fuel_included"),
        migrations.AlterField("listing", "fuel_included", models.BooleanField(blank=True, null=True)),
        migrations.AlterField("listing", "transport_included", models.BooleanField(blank=True, null=True)),
        migrations.AddField("listing", "operator_answer", models.BooleanField(blank=True, null=True)),
        migrations.RunPython(clean, migrations.RunPython.noop),
    ]
