"""seed_listings: only on a DEBUG (dev) database, and --delete removes exactly what it made."""

import pytest
from django.core.management import CommandError, call_command

from listings.models import Listing


def test_refuses_without_debug(db, settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_listings", count=5)
    assert Listing.objects.count() == 0


def test_creates_and_deletes_only_its_own(settings, listing_id):
    settings.DEBUG = True
    call_command("seed_listings", count=30, spread_km=20)
    assert Listing.objects.count() == 31
    call_command("seed_listings", delete=True)
    assert list(Listing.objects.values_list("id", flat=True)) == [listing_id]  # the real owner's listing stays
