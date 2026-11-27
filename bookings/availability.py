"""
When is a machine free? The ONE place that answers it (docs/05-data-model.md §4–§5, 06 §6).
The calendar, the start-time chips, date search and the create check all use these functions.

Times: the owner and the renter think in whole IST hours on a date ("सुबह 8 बजे"). The database stores UTC.
`at(day, hour)` turns one into the other; hour 24 = the next midnight.

    open hours of a date = its DayOverride if one exists, else that weekday's WeeklyHours, else closed
    a booking reserves   [start_at, blocked_until)  = work time + the owner's gap
    "active"             = accepted, or requested and not past expires_at
    a start hour s fits  = inside the open hours (the gap may run past closing), its own reserved range
                           [s, s + hours + gap) touches no active booking's range, and s isn't in the past
"""

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone

from core.errors import ApiError
from listings.models import DayOverride, Listing

from .models import Booking

IST = ZoneInfo("Asia/Kolkata")
WINDOW_DAYS = 30  # bookings, the calendar and day changes all look at most 30 days ahead (decision §11)
MAX_HOURS = 12  # a booking is always whole hours (decision §23); the day is 7–19, so 12 is the most there is


# --------------------------------------------------------------------- time helpers

def today_ist(now: datetime | None = None) -> date:
    return (now or timezone.now()).astimezone(IST).date()


def window(now: datetime | None = None) -> tuple[date, date]:
    """Today and the last bookable day (today + 29), IST."""
    first = today_ist(now)
    return first, first + timedelta(days=WINDOW_DAYS - 1)


def at(day: date, hour: int) -> datetime:
    """A whole IST hour on a date as an aware datetime. hour 24 = the next midnight. (IST has no summer time.)"""
    return datetime.combine(day, time(0), tzinfo=IST) + timedelta(hours=hour)


def hour_of(day: date, moment: datetime) -> int:
    """How many whole hours after the day's IST midnight `moment` is (may be < 0 or > 24)."""
    return math.floor((moment - at(day, 0)) / timedelta(hours=1))


def next_whole_hour(now: datetime) -> datetime:
    """The earliest start a renter may pick: now if it's exactly on the hour, else the next whole IST hour.
    Rounded in IST: India is 5½ h ahead of UTC, so a whole UTC hour is half past in IST."""
    now = now.astimezone(IST)
    floor = now.replace(minute=0, second=0, microsecond=0)
    return floor if floor == now else floor + timedelta(hours=1)


def active_q(now: datetime) -> Q:
    return Q(status=Booking.Status.ACCEPTED) | Q(status=Booking.Status.REQUESTED, expires_at__gt=now)


# --------------------------------------------------------------------- rules

def expires_at_for(start_at: datetime, now: datetime) -> datetime:
    """How long the owner has to answer (owner, 2026-11-23, decision §21):
    2 hours when the work is today or tomorrow, 6 hours when it's 2+ days ahead, always before the start."""
    days_ahead = (start_at.astimezone(IST).date() - today_ist(now)).days
    wait = timedelta(hours=2 if days_ahead <= 1 else 6)
    return min(now + wait, start_at)


def check_hours(hours: int) -> int:
    """Decision §23 (owner, 2026-11-27): renters always book whole hours (1, 2, 3 …), whatever the price unit.
    Acres and days are only shown as the owner's price; the final price is agreed on the phone."""
    if not isinstance(hours, int) or not 1 <= hours <= MAX_HOURS:
        raise ApiError("validation_error", fields={"hours": "whole_hours_1_12"})
    return hours


# --------------------------------------------------------------------- one listing over a range of dates

@dataclass
class Busy:
    start_hour: int
    end_hour: int
    booking: Booking


@dataclass
class Plan:
    """Everything needed to answer "free?" for one listing between two dates, loaded in 3 queries."""

    listing: Listing
    now: datetime
    weekly: dict[int, tuple[int, int]]
    overrides: dict[date, DayOverride]
    bookings: list[Booking] = field(default_factory=list)

    # -- open hours

    def open_hours(self, day: date) -> tuple[int, int] | None:
        if not self.listing.has_schedule:
            return None
        override = self.overrides.get(day)
        if override is not None:
            return None if override.is_closed else (override.start_hour, override.end_hour)
        return self.weekly.get(day.weekday())  # Python: Monday = 0 = सोम, like our weekdays

    # -- taken hours

    def busy(self, day: date) -> list[Busy]:
        """Reserved hours (work + gap) that fall on this date, clipped to its 0–24, earliest first."""
        out = []
        for b in self.bookings:
            s, e = max(hour_of(day, b.start_at), 0), min(hour_of(day, b.blocked_until), 24)
            if s < e:
                out.append(Busy(s, e, b))
        return out

    def clashes(self, start: datetime, end: datetime, *, ignore: set[int] = frozenset()) -> bool:
        """Does the reserved range [start, end) touch an active booking's [start_at, blocked_until)?"""
        return any(b.start_at < end and b.blocked_until > start for b in self.bookings if b.pk not in ignore)

    # -- answers

    def status(self, day: date) -> str:
        """Calendar colour: closed (बंद) / booked (बुक है: some or all open hours taken) / free (खाली)."""
        open_ = self.open_hours(day)
        if open_ is None:
            return "closed"
        if any(b.start_hour < open_[1] and b.end_hour > open_[0] for b in self.busy(day)):
            return "booked"
        return "free"

    def start_hours(self, day: date, hours: int) -> list[int]:
        """The start-time chips for a job of `hours` on `day` (05 §4 rules 1–4)."""
        open_ = self.open_hours(day)
        if open_ is None or hours > open_[1] - open_[0]:
            return []
        earliest = next_whole_hour(self.now)
        reserve = timedelta(hours=hours + self.listing.gap_hours)
        return [
            s for s in range(open_[0], open_[1] - hours + 1)
            if at(day, s) >= earliest and not self.clashes(at(day, s), at(day, s) + reserve)
        ]

    def max_hours(self, day: date, start: int) -> int:
        """S11 "कितने घंटे?": the longest job that may start at `start` on `day` (0 = none). Ends at closing time
        (7 PM) or where the next booking (with this job's own 1-hour rest gap) begins, by the same rule as the chips."""
        open_ = self.open_hours(day)
        if open_ is None or not open_[0] <= start < open_[1] or at(day, start) < next_whole_hour(self.now):
            return 0
        gap = self.listing.gap_hours
        best = 0
        for h in range(1, open_[1] - start + 1):
            if self.clashes(at(day, start), at(day, start + h + gap)):
                break
            best = h
        return best


def load_plan(listing: Listing, first: date, last: date, now: datetime | None = None) -> Plan:
    now = now or timezone.now()
    weekly = {w.weekday: (w.start_hour, w.end_hour) for w in listing.weekly_hours.all()}
    overrides = {o.date: o for o in DayOverride.objects.filter(listing=listing, date__range=(first, last))}
    bookings = list(
        Booking.objects.filter(listing=listing)
        .filter(active_q(now))
        # A booking late the day before can spill its gap into `first`, so compare reserved ranges.
        .filter(start_at__lt=at(last, 24), blocked_until__gt=at(first, 0))
        .order_by("start_at")
    )
    return Plan(listing=listing, now=now, weekly=weekly, overrides=overrides, bookings=bookings)


def work_hours_on(day: date, booking: Booking) -> tuple[int, int]:
    """A booking's work time [start, end) as hours of its own date (a job never crosses midnight)."""
    return hour_of(day, booking.start_at), hour_of(day, booking.end_at)


def bookings_outside(listing: Listing, now: datetime, open_for_day) -> list[int]:
    """Ids of active, not-yet-finished bookings whose WORK no longer fits the new open hours.
    `open_for_day(day)` gives the new (start, end) or None (closed), or False = "this day isn't changing".
    The gap may run past closing, so only the work time must fit (05 §4 rule 2)."""
    ids = []
    for b in Booking.objects.filter(listing=listing, end_at__gt=now).filter(active_q(now)).order_by("start_at"):
        day = b.start_at.astimezone(IST).date()
        new = open_for_day(day)
        if new is False:
            continue
        s, e = work_hours_on(day, b)
        if new is None or s < new[0] or e > new[1]:
            ids.append(b.pk)
    return ids


# --------------------------------------------------------------------- many listings at once (search)

def plans_for(listings: list[Listing], first: date, last: date, now: datetime | None = None) -> dict[int, Plan]:
    """load_plan() for many listings in 3 queries (not 3 per listing)."""
    from listings.models import WeeklyHours

    now = now or timezone.now()
    ids = [x.pk for x in listings]
    weekly: dict[int, dict] = {i: {} for i in ids}
    for w in WeeklyHours.objects.filter(listing_id__in=ids):
        weekly[w.listing_id][w.weekday] = (w.start_hour, w.end_hour)
    overrides: dict[int, dict] = {i: {} for i in ids}
    for o in DayOverride.objects.filter(listing_id__in=ids, date__range=(first, last)):
        overrides[o.listing_id][o.date] = o
    bookings: dict[int, list] = {i: [] for i in ids}
    for b in (Booking.objects.filter(listing_id__in=ids).filter(active_q(now))
              .filter(start_at__lt=at(last, 24), blocked_until__gt=at(first, 0)).order_by("start_at")):
        bookings[b.listing_id].append(b)
    return {x.pk: Plan(listing=x, now=now, weekly=weekly[x.pk], overrides=overrides[x.pk], bookings=bookings[x.pk])
            for x in listings}


def open_on_q(day: date) -> Q:
    """SQL pre-check for search: has hours, and open on `day` (a changed day's hours, else its weekday's).
    Cheap and index-friendly; the exact "is an hour still free?" check follows in Python (free_ids)."""
    from django.db.models import Exists, OuterRef

    from listings.models import WeeklyHours

    override = DayOverride.objects.filter(listing=OuterRef("pk"), date=day)
    weekly = WeeklyHours.objects.filter(listing=OuterRef("pk"), weekday=day.weekday())
    return Q(has_schedule=True) & (
        Exists(override.filter(is_closed=False)) | (Exists(weekly) & ~Exists(override))
    )


def free_ids(listings, day: date, now: datetime | None = None, *, stop_at_first=False) -> list[int]:
    """Of these listings, the ones with at least one free start hour on `day` for a 1-hour job (decision §21:
    "कब चाहिए?" is a date, so a machine counts as free that day if ANY hour is still free; S11 then shows
    exactly which). Same rules as the start-time chips: open hours, other bookings + gaps, not in the past."""
    now = now or timezone.now()
    listings = list(listings.filter(open_on_q(day)).only("pk", "has_schedule", "gap_hours", "status"))
    free = []
    for pk, plan in plans_for(listings, day, day, now).items():
        if plan.start_hours(day, 1):
            free.append(pk)
            if stop_at_first:
                break
    return free
