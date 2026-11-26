"""The data migration 0005 (decision §22) on old-style listings: mixed units, no hours, gap 0, min 3 hours."""

import importlib
from decimal import Decimal

from django.apps import apps

from listings.models import Listing, ListingOption

from .conftest import mt

simplify = importlib.import_module("listings.migrations.0005_simple_terms").simplify


def old_listing(owner, code="tractor", has_schedule=False, gap=0):
    return Listing.objects.create(owner=owner, machine_type=mt(code), lat="25.4", lng="81.9", gap_hours=gap,
                                  fuel_included=False, transport_included=False, has_schedule=has_schedule)


def option(listing, unit, implement=None, price="900"):
    return ListingOption.objects.create(listing=listing, implement=mt(implement) if implement else None,
                                        price_unit=unit, price=Decimal(price), min_hours=3,
                                        hours_per_acre=Decimal("1.5") if unit == "acre" else None)


def test_old_listing_gets_the_fixed_terms(owner):
    listing = old_listing(owner)
    hour = option(listing, "hour")
    rot_hour = option(listing, "hour", "rotavator")
    rot_acre = option(listing, "acre", "rotavator")
    simplify(apps, None)
    listing.refresh_from_db()
    assert (listing.gap_hours, listing.fuel_included, listing.transport_included) == (1, True, True)
    # Most prices are per hour: those stay; the एकड़ one is switched off (not deleted: bookings may point at it).
    assert set(listing.options.filter(is_active=True)) == {hour, rot_hour}
    assert ListingOption.objects.filter(pk=rot_acre.pk, is_active=False).exists()
    assert set(listing.options.values_list("min_hours", flat=True)) == {1}
    assert listing.has_schedule and listing.weekly_hours.count() == 7
    assert {(w.start_hour, w.end_hour) for w in listing.weekly_hours.all()} == {(7, 19)}


def test_a_tie_keeps_per_hour(owner):
    listing = old_listing(owner)
    option(listing, "acre")
    option(listing, "hour", "rotavator")
    simplify(apps, None)
    assert set(listing.options.filter(is_active=True).values_list("price_unit", flat=True)) == {"hour"}


def test_own_hours_are_kept_and_a_drone_keeps_fuel_null(owner):
    drone = old_listing(owner, "drone_sprayer", has_schedule=True)
    drone.weekly_hours.create(weekday=0, start_hour=6, end_hour=10)
    option(drone, "hour")
    simplify(apps, None)
    drone.refresh_from_db()
    assert drone.fuel_included is None and drone.transport_included is True
    assert [(w.weekday, w.start_hour, w.end_hour) for w in drone.weekly_hours.all()] == [(0, 6, 10)]
