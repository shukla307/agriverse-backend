"""
Who hears about a booking change (Phase 5 slice 6). Called wherever a BookingEvent is written, inside the same
transaction, so a change and its notifications are saved together or not at all:

    requests.create   → requested            actions.change     → accepted / rejected / cancelled / completed
    expire_overdue    → expired (system)     complete_done      → completed (system)

Rule: the OTHER person hears about it, never the one who did it. A change made by the system (actor None)
reaches both. A per-day group (old bookings) gives ONE notification, pointing at its first day.
Slice 7: each saved row is also pushed to that person's phones, AFTER the transaction is saved (push.py).
"""

from collections.abc import Iterable

from bookings.models import Booking
from bookings.serializers import first_name, ist

from . import push
from .models import Notification

S = Booking.Status
T = Notification.Type

# to_status → [(who, type)]
WHO: dict[str, list[tuple[str, str]]] = {
    S.REQUESTED: [("owner", T.BOOKING_REQUESTED)],
    S.ACCEPTED: [("renter", T.BOOKING_ACCEPTED)],
    S.REJECTED: [("renter", T.BOOKING_REJECTED)],
    S.EXPIRED: [("renter", T.BOOKING_EXPIRED), ("owner", T.BOOKING_EXPIRED_OWNER)],
    S.CANCELLED_BY_RENTER: [("owner", T.BOOKING_CANCELLED_BY_RENTER)],
    S.CANCELLED_BY_OWNER: [("renter", T.BOOKING_CANCELLED_BY_OWNER)],
    S.COMPLETED: [("renter", T.BOOKING_COMPLETED), ("owner", T.BOOKING_COMPLETED)],
}


def params(rows: list[Booking]) -> dict:
    """What the sentence needs, frozen now (a later name change doesn't rewrite old notifications)."""
    b = rows[0]
    listing, o = b.listing, b.option
    implement = None
    if o.implement_id is not None:
        implement = o.implement_other or o.implement.name_hi
    return {
        "renter": first_name(b.renter),
        "owner": first_name(listing.owner),
        "machine": listing.machine_type_other or listing.machine_type.name_hi,
        "implement": implement,
        "start": ist(b.start_at),
        "end": ist(max(r.end_at for r in rows)),
    }


def booking_changed(bookings: Iterable[int | Booking], to_status: str, actor=None) -> list[Notification]:
    ids = [b.pk if isinstance(b, Booking) else b for b in bookings]
    if not ids or to_status not in WHO:
        return []
    rows = (
        Booking.objects.filter(pk__in=ids)
        .select_related("listing__machine_type", "listing__owner", "option__implement", "renter")
        .order_by("start_at", "pk")
    )
    groups: dict = {}
    for b in rows:
        groups.setdefault(b.group_id or f"#{b.pk}", []).append(b)
    actor_id = getattr(actor, "pk", None)
    out = []
    for group in groups.values():
        first = group[0]
        snapshot = params(group)
        for who, kind in WHO[to_status]:
            user_id = first.listing.owner_id if who == "owner" else first.renter_id
            if user_id == actor_id:
                continue  # you don't get told what you just did
            out.append(Notification(user_id=user_id, booking=first, type=kind, role=who, params=snapshot))
    saved = Notification.objects.bulk_create(out)
    push.after_commit(saved)  # slice 7: to their phones, once this transaction is saved
    return saved
