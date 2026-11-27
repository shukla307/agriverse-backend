"""
Shared set-up for booking tests. The clock is frozen at **Monday 2026-11-23, सुबह 7:30 IST** (02:00 UTC),
so "today" and "the past" are the same in every run.

The machine: Ramesh's tractor, open every day सुबह 6 – शाम 6, gap 1 hour, with 4 ways to book it.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from freezegun import freeze_time

from accounts.models import Address, User
from bookings.availability import at
from bookings.models import Booking
from catalog.models import JobCategory, MachineType
from listings.models import Listing, ListingOption, WeeklyHours

NOW_UTC = "2026-11-23 02:00:00"  # = 07:30 IST, a Monday
MON = date(2026, 11, 23)
TUE = MON + timedelta(days=1)
WED = MON + timedelta(days=2)


@pytest.fixture(autouse=True)
def frozen():
    with freeze_time(NOW_UTC) as f:
        yield f


def mt(code) -> MachineType:
    return MachineType.objects.get(code=code)


@pytest.fixture
def owner(db):
    user = User.objects.create_user(phone="+919876543210", firebase_uid="uid-owner", name="Ramesh")
    user.home_address = Address.objects.create(
        user=user, label="home", lat="25.438000", lng="81.905000", village="झूंसी", landmark="मंदिर के पास"
    )
    user.save()
    return user


@pytest.fixture
def renter(db):
    user = User.objects.create_user(phone="+919812345678", firebase_uid="uid-renter", name="Suresh")
    user.home_address = Address.objects.create(user=user, label="home", lat="25.50", lng="81.85", village="सोरांव")
    user.save()
    return user


def set_week(listing, start=6, end=18, weekdays=range(7)):
    WeeklyHours.objects.filter(listing=listing).delete()
    WeeklyHours.objects.bulk_create(WeeklyHours(listing=listing, weekday=w, start_hour=start, end_hour=end)
                                    for w in weekdays)
    listing.has_schedule = True
    listing.save()


@pytest.fixture
def tractor(owner):
    listing = Listing.objects.create(
        owner=owner, machine_type=mt("tractor"), lat="25.438000", lng="81.905000", village="झूंसी",
        landmark="मंदिर के पास", gap_hours=1, fuel_included=False, transport_included=True,
    )
    set_week(listing)
    listing.opt_hour = ListingOption.objects.create(listing=listing, price_unit="hour", price=600, min_hours=2)
    listing.opt_acre = ListingOption.objects.create(  # owner's own value: 1.5 h per acre
        listing=listing, implement=mt("rotavator"), price_unit="acre", price=1500, hours_per_acre=Decimal("1.5")
    )
    listing.opt_cult = ListingOption.objects.create(
        listing=listing, implement=mt("cultivator"), price_unit="hour", price=800, min_hours=1
    )
    listing.opt_day = ListingOption.objects.create(listing=listing, price_unit="day", price=5000)
    return listing


def book(listing, option, renter, day, start, end, status=Booking.Status.ACCEPTED, expires_at=None, gap=None):
    """A booking written straight to the database (creation through the API comes in slice 3)."""
    gap = listing.gap_hours if gap is None else gap
    return Booking.objects.create(
        listing=listing, option=option, renter=renter, job=JobCategory.objects.get(code="land_prep"),
        status=status, start_at=at(day, start), end_at=at(day, end),
        blocked_until=at(day, end) + timedelta(hours=gap), quantity=end - start, hours=end - start,
        price_unit=option.price_unit, unit_price=option.price, field_lat="25.5", field_lng="81.85",
        expires_at=expires_at or at(day, start),
    )


@pytest.fixture
def as_renter(api, renter):
    api.force_authenticate(renter)
    return api


@pytest.fixture
def as_owner(api, owner):
    api.force_authenticate(owner)
    return api


def starts(client, listing, day):
    """S11's start times + how long a job from each may last (decision §23)."""
    return client.get(f"/api/listings/{listing.pk}/start-times/", {"date": day.isoformat()})
