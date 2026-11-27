"""The data migration 0006 (decision §23, BUGS.md #19): every machine every day 7–19, whatever it had."""

import importlib
from datetime import date

from django.apps import apps

from listings.models import DayOverride, Listing

from .conftest import mt

fix_hours = importlib.import_module("listings.migrations.0006_fixed_hours").fix_hours


def listing_with(owner, hours, code="tractor", status="active"):
    listing = Listing.objects.create(owner=owner, machine_type=mt(code), lat="25.4", lng="81.9", status=status,
                                     has_schedule=bool(hours))
    for weekday, (s, e) in hours.items():
        listing.weekly_hours.create(weekday=weekday, start_hour=s, end_hour=e)
    return listing


def week(listing):
    return sorted((w.weekday, w.start_hour, w.end_hour) for w in listing.weekly_hours.all())


EVERY_DAY_7_19 = [(d, 7, 19) for d in range(7)]


def test_the_bug_tractor_open_day_and_night_gets_7_to_19(owner):
    tractor = listing_with(owner, {d: (0, 24) for d in range(7)})  # "रात 12 बजे से रात 12 बजे तक"
    fix_hours(apps, None)
    assert week(tractor) == EVERY_DAY_7_19


def test_older_hours_and_missing_days_and_no_hours(owner):
    six_to_six = listing_with(owner, {d: (6, 18) for d in range(7)})
    mornings_some_days = listing_with(owner, {0: (6, 12), 3: (6, 12)}, code="drone_sprayer")
    none = listing_with(owner, {})
    paused = listing_with(owner, {0: (9, 10)}, status="paused")
    fix_hours(apps, None)
    for listing in (six_to_six, mornings_some_days, none, paused):
        listing.refresh_from_db()
        assert week(listing) == EVERY_DAY_7_19 and listing.has_schedule


def test_day_changes_with_other_hours_go_closed_days_stay(owner):
    listing = listing_with(owner, {d: (6, 18) for d in range(7)})
    DayOverride.objects.create(listing=listing, date=date(2026, 12, 1), is_closed=False, start_hour=5, end_hour=22)
    DayOverride.objects.create(listing=listing, date=date(2026, 12, 2), is_closed=True)
    fix_hours(apps, None)
    assert list(DayOverride.objects.values_list("date", "is_closed")) == [(date(2026, 12, 2), True)]
