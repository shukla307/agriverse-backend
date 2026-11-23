"""Layer 3 (05 §5): the DATABASE refuses two active bookings whose reserved ranges overlap on one machine,
even if the code had a bug. These tests write straight to the database, past every check in Python."""

import pytest
from django.db import IntegrityError, transaction

from bookings.models import Booking

from .conftest import TUE, book


def test_overlap_refused(tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
    with pytest.raises(IntegrityError), transaction.atomic():
        book(tractor, tractor.opt_cult, renter, TUE, 10, 12)  # another option, same tractor


def test_the_gap_is_part_of_the_range(tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11)  # reserves 8–12
    with pytest.raises(IntegrityError), transaction.atomic():
        book(tractor, tractor.opt_hour, renter, TUE, 11, 13)
    book(tractor, tractor.opt_hour, renter, TUE, 12, 14)  # 12 is fine


def test_no_gap_back_to_back_is_fine(tractor, renter):
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11, gap=0)
    book(tractor, tractor.opt_hour, renter, TUE, 11, 13, gap=0)


def test_finished_statuses_dont_hold_the_machine(tractor, renter):
    for s in ("rejected", "expired", "cancelled_by_renter", "cancelled_by_owner", "completed"):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=s)
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=Booking.Status.REQUESTED)


def test_other_machines_are_separate(tractor, renter, owner):
    from listings.models import Listing, ListingOption

    other = Listing.objects.create(owner=owner, machine_type=tractor.machine_type, lat="25.4", lng="81.9")
    opt = ListingOption.objects.create(listing=other, price_unit="hour", price=500)
    book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
    book(other, opt, renter, TUE, 8, 11)
