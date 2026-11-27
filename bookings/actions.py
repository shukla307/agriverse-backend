"""
Changing a booking after it was sent (Phase 5 slice 4; docs/06-api.md §8 "Status actions", 05 §5).

    RULES           who may do what, from which status, to which status: the ONE table (tested row by row)
    act()           one action under the listing lock: overdue requests are expired first, then the rule is
                    checked and every day of a per-day group changes together, each with a BookingEvent
    expire_overdue  requests the owner never answered stop holding the machine (lazy: every read and write
                    calls it; run_booking_jobs does the same every few minutes once it runs on Render)
    complete_done   accepted work 12 h after it ended → completed (run_booking_jobs)

Errors: 409 `expired` (the owner answers too late), 409 `invalid_status` (someone acted first, e.g. the renter
cancelled, or the owner accepted while the renter cancelled without a reason; `fields.status` = what it is now),
400 `reason: required` (reject, any cancel of an accepted booking) / `reason: invalid` (the other side's chip),
409 `not_started` (काम पूरा हुआ before the work began),
403 `permission_denied` (e.g. a renter tries to accept), 404 for anyone who isn't the renter or the owner.
Every change also leaves a notification for the other person (notifications/notify.py, slice 6); pushes: slice 7.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status

from core.errors import ApiError
from listings.models import Listing
from notifications.notify import booking_changed

from .models import Booking, BookingEvent

S = Booking.Status
CONFLICT = status.HTTP_409_CONFLICT
AUTO_COMPLETE_AFTER = timedelta(hours=12)  # 06 §8: accepted work is completed 12 h after it ended


@dataclass(frozen=True)
class Rule:
    allowed_from: tuple[str, ...]
    to: str
    reason_from: tuple[str, ...] = ()  # a reason chip is required when the booking is in one of these


# (action, who) → rule. Anything missing is not allowed (403).
RULES: dict[tuple[str, str], Rule] = {
    ("accept", "owner"): Rule((S.REQUESTED,), S.ACCEPTED),
    ("reject", "owner"): Rule((S.REQUESTED,), S.REJECTED, reason_from=(S.REQUESTED,)),
    # Slice 5: a waiting request is cancelled with one tap; a CONFIRMED one only with a reason (decision §21:
    # talk first, then the reason, like the owner).
    ("cancel", "renter"): Rule((S.REQUESTED, S.ACCEPTED), S.CANCELLED_BY_RENTER, reason_from=(S.ACCEPTED,)),
    ("cancel", "owner"): Rule((S.ACCEPTED,), S.CANCELLED_BY_OWNER, reason_from=(S.ACCEPTED,)),
    ("complete", "owner"): Rule((S.ACCEPTED,), S.COMPLETED),
}
ACTIONS = sorted({a for a, _ in RULES})

R = Booking.Reason
# Each side has its own chips (glossary §6); "कोई और वजह" ends both lists.
REASONS = {
    "owner": {R.BUSY, R.REPAIR, R.TOO_FAR, R.OTHER},
    "renter": {R.NOT_NEEDED, R.FOUND_OTHER, R.WEATHER, R.OTHER},
}


def expire_overdue(qs: QuerySet, now: datetime) -> list[int]:
    """Mark `requested` rows in `qs` whose answer time has passed as expired, each with its history row.
    Rows another transaction holds (an owner accepting right now) are skipped, not waited for: that one
    decides for itself under the listing lock."""
    with transaction.atomic():
        ids = list(
            qs.filter(status=S.REQUESTED, expires_at__lte=now)
            .select_for_update(skip_locked=True, of=("self",))
            .values_list("pk", flat=True)
        )
        if ids:
            Booking.objects.filter(pk__in=ids).update(status=S.EXPIRED)
            BookingEvent.objects.bulk_create(
                BookingEvent(booking_id=pk, from_status=S.REQUESTED, to_status=S.EXPIRED) for pk in ids
            )
            booking_changed(ids, S.EXPIRED)  # the system did it: both hear about it
    return ids


def role_of(booking: Booking, user) -> str:
    return "owner" if booking.listing.owner_id == user.id else "renter"


def act(*, booking_id: int, user, action: str, reason: str = "", reason_other: str = "",
        now: datetime | None = None) -> Booking:
    now = now or timezone.now()
    b = get_object_or_404(
        Booking.objects.select_related("listing").filter(Q(renter=user) | Q(listing__owner=user)), pk=booking_id
    )  # anyone else: 404, so ids can't be probed (06 §1)
    who = role_of(b, user)
    rule = RULES.get((action, who))
    if rule is None:
        raise ApiError("permission_denied", status.HTTP_403_FORBIDDEN)
    if reason and reason not in REASONS[who]:
        raise ApiError("validation_error", fields={"reason": "invalid"})
    if b.status in rule.reason_from and not reason:
        raise ApiError("validation_error", fields={"reason": "required"})

    refused = None  # raised only AFTER the transaction is saved, so the expiry below is never rolled back
    with transaction.atomic():
        # The same lock as a new booking and the owner's day/week changes (05 §5): one change at a time.
        Listing.objects.select_for_update(of=("self",)).get(pk=b.listing_id)
        expire_overdue(Booking.objects.filter(listing_id=b.listing_id), now)
        rows = list(
            Booking.objects.filter(group_id=b.group_id) if b.group_id else Booking.objects.filter(pk=b.pk)
        )
        current = next(r for r in rows if r.pk == b.pk).status
        if current not in rule.allowed_from:
            if current == S.EXPIRED and action == "accept":
                refused = ApiError("expired", CONFLICT)
            else:
                refused = ApiError("invalid_status", CONFLICT, fields={"status": current})
        elif current in rule.reason_from and not reason:
            # Changed meanwhile into a status that needs a reason (the owner accepted while the renter tapped
            # S12's one-tap cancel): say "it changed" so the app reloads and asks properly.
            refused = ApiError("invalid_status", CONFLICT, fields={"status": current})
        elif action == "complete" and now < min(r.start_at for r in rows):
            refused = ApiError("not_started", CONFLICT)
        else:
            changed = [r for r in rows if r.status in rule.allowed_from]  # skips days already completed
            for r in changed:
                change(r, rule, action, user, reason, reason_other, now)
            booking_changed(changed, rule.to, actor=user)
    if refused:
        raise refused
    return b


def change(r: Booking, rule: Rule, action: str, user, reason: str, reason_other: str, now: datetime):
    before = r.status
    r.status = rule.to
    if action in ("accept", "reject"):
        r.responded_at = now
    if action == "accept":
        r.accepted_at = now
    elif action == "cancel":
        r.cancelled_at = now
    elif action == "complete":
        r.completed_at = now
    if reason:
        r.reason, r.reason_other = reason, reason_other
    r.save()
    BookingEvent.objects.create(
        booking=r, from_status=before, to_status=rule.to, actor=user, reason=reason, reason_other=reason_other
    )


def complete_done(now: datetime) -> list[int]:
    """Accepted work that ended 12+ hours ago → completed (system). A per-day group waits for its last day."""
    cutoff = now - AUTO_COMPLETE_AFTER
    with transaction.atomic():
        due = list(
            Booking.objects.filter(status=S.ACCEPTED, end_at__lte=cutoff)
            .select_for_update(skip_locked=True)
            .values_list("pk", "group_id")
        )
        unfinished = set(
            Booking.objects.filter(group_id__in={g for _, g in due if g}, status=S.ACCEPTED, end_at__gt=cutoff)
            .values_list("group_id", flat=True)
        )
        ids = [pk for pk, g in due if g not in unfinished]
        if ids:
            Booking.objects.filter(pk__in=ids).update(status=S.COMPLETED, completed_at=now)
            BookingEvent.objects.bulk_create(
                BookingEvent(booking_id=pk, from_status=S.ACCEPTED, to_status=S.COMPLETED) for pk in ids
            )
            booking_changed(ids, S.COMPLETED)
    return ids
