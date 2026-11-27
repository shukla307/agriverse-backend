"""
Sending a booking request (Phase 5 slice 3; docs/05-data-model.md §5 layer 2, 06 §8).

    Decision §23 (owner, 2026-11-27): a booking is ONE price (machine alone, or machine + one attachment), a start
    hour and whole hours. Acres and days are the owner's price only (the final price is agreed on the phone), and
    the job comes from the machine (auto_job), not from the renter.

    check()   what the renter asks for → the exact rows to reserve, or an ApiError. Used by quote/ (no lock,
              nothing held) and by create() (under the lock, so the answer can't change before the insert).
    create()  the locked check-then-insert: one request at a time per machine, stale requests expired first,
              the same rules as the start-time chips, then the rows. The database's no-overlap rule (layer 3)
              is the last guard; if it fires anyway, the renter gets the same 409 slot_taken.

Errors the app turns into one calm message + fresh start times: slot_taken (taken a moment ago, or the hour
has passed), outside_hours (the owner changed the hours meanwhile).
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status

from core.errors import ApiError
from catalog.models import JobCategory
from listings.models import Listing, ListingOption

from .actions import expire_overdue
from .availability import IST, Plan, at, check_hours, expires_at_for, hour_of, load_plan, next_whole_hour, window
from .models import Booking, BookingEvent

CONFLICT = status.HTTP_409_CONFLICT


@dataclass
class Slot:
    start_at: datetime
    end_at: datetime


@dataclass
class Checked:
    """What one request would reserve: one slot of whole hours."""

    listing: Listing
    option: ListingOption
    hours: int
    slots: list[Slot]

    @property
    def gap(self) -> timedelta:
        return timedelta(hours=self.listing.gap_hours)

    def row_total(self) -> Decimal | None:
        """Only an hourly price gives an estimate (price × hours). Per एकड़ / दिन / मोल-भाव: agreed on the phone."""
        if self.option.price_unit != ListingOption.Unit.HOUR or self.option.price is None:
            return None
        return (self.option.price * self.hours).quantize(Decimal("0.01"))

    def total(self) -> Decimal | None:
        return self.row_total()


def auto_job(option: ListingOption) -> tuple[JobCategory, str]:
    """Decision §23: the renter no longer picks the job. The attachment's first job, else the machine's; a
    machine without jobs (अन्य) → "other" with the machine's name."""
    for m in (option.implement, option.listing.machine_type):
        if m is not None:
            job = m.jobs.filter(is_active=True).order_by("sort_order").first()
            if job:
                return job, ""
    name = option.listing.machine_type_other or option.listing.machine_type.name_hi
    return JobCategory.objects.get(code="other"), name[:60]


def find_option(option_id: int, user) -> ListingOption:
    """An active price on a listing anyone may book (not removed, owner not deleted), else validation_error."""
    option = (
        ListingOption.objects.select_related("listing__machine_type", "implement")
        .filter(pk=option_id, is_active=True, listing__owner__is_active=True)
        .exclude(listing__status=Listing.Status.REMOVED)
        .first()
    )
    if option is None:
        raise ApiError("validation_error", fields={"option_id": "unknown"})
    if option.listing.owner_id == user.id:
        raise ApiError("own_listing")
    return option


def first_day(start_at: datetime, now: datetime) -> date:
    local = start_at.astimezone(IST)
    if local.minute or local.second or local.microsecond:
        raise ApiError("not_whole_hour", fields={"start_at": "not_whole_hour"})
    day = local.date()
    first, last = window(now)
    if not first <= day <= last:
        raise ApiError("validation_error", fields={"start_at": "outside_30_days"})
    return day


def check(option: ListingOption, start_at: datetime, hours: int, now: datetime, *,
          listing: Listing | None = None) -> Checked:
    """The same rules as the start-time chips (05 §4), for one exact request. `listing` = the locked row
    when called from create(), so a pause or a new schedule that just happened is seen."""
    listing = listing or option.listing
    if not listing.bookable:
        raise ApiError("not_bookable", CONFLICT)
    day = first_day(start_at, now)
    hours = check_hours(hours)
    plan = load_plan(listing, day, day, now)
    return Checked(listing=listing, option=option, hours=hours, slots=[hour_slot(plan, day, start_at, hours)])


def hour_slot(plan: Plan, day: date, start_at: datetime, hours: int) -> Slot:
    open_ = plan.open_hours(day)
    s = hour_of(day, start_at)
    if open_ is None or s < open_[0] or s + hours > open_[1]:  # only the work must fit; the gap may run past
        raise ApiError("outside_hours", CONFLICT)
    start, end = at(day, s), at(day, s + hours)
    if start < next_whole_hour(plan.now):
        raise ApiError("slot_taken", CONFLICT, fields={"start_at": "passed"})
    if plan.clashes(start, end + timedelta(hours=plan.listing.gap_hours)):
        raise ApiError("slot_taken", CONFLICT)
    return Slot(start, end)


def expire_stale(listing: Listing, now: datetime) -> int:
    """Lazy expiry (05 §5): requests past their answer time stop holding the machine, before anything new is
    written. Each gets its history row. The push to the renter comes with notifications (slice 6)."""
    return len(expire_overdue(Booking.objects.filter(listing=listing), now))


def create(*, renter, option: ListingOption, start_at: datetime, hours: int, field: dict,
           now: datetime | None = None) -> list[Booking]:
    now = now or timezone.now()
    with transaction.atomic():
        # Layer 2: one request at a time per machine (the same lock as the owner's day / week changes).
        listing = Listing.objects.select_for_update(of=("self",)).select_related("machine_type").get(pk=option.listing_id)
        if listing.status == Listing.Status.REMOVED:
            raise ApiError("not_bookable", CONFLICT)
        expire_stale(listing, now)
        checked = check(option, start_at, hours, now, listing=listing)
        expires_at = expires_at_for(checked.slots[0].start_at, now)
        job, job_other = auto_job(option)
        rows = []
        try:
            with transaction.atomic():  # savepoint: the no-overlap rule (layer 3) may still refuse
                for slot in checked.slots:
                    b = Booking.objects.create(
                        listing=listing, option=option, renter=renter, job=job, job_other=job_other,
                        start_at=slot.start_at, end_at=slot.end_at, blocked_until=slot.end_at + checked.gap,
                        quantity=checked.hours, hours=checked.hours,
                        price_unit=option.price_unit, unit_price=option.price,
                        estimated_total=checked.row_total(),
                        field_lat=field["lat"], field_lng=field["lng"],
                        field_village=field.get("village", ""), field_landmark=field.get("landmark", ""),
                        expires_at=expires_at,
                    )
                    BookingEvent.objects.create(booking=b, to_status=Booking.Status.REQUESTED, actor=renter)
                    rows.append(b)
        except IntegrityError:
            raise ApiError("slot_taken", CONFLICT)
    return rows
