"""Switch on PostgreSQL's pg_trgm (misspelt brands / typed names in search, 05 §6).
It's a "trusted" extension (PostgreSQL 13+), so the database owner can enable it; no superuser needed."""

from django.contrib.postgres.operations import TrigramExtension
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("listings", "0001_listings_options_photos_hours")]
    operations = [TrigramExtension()]
