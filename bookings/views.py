"""
Availability (docs/06-api.md §6) and booking requests (06 §8, Phase 5 slice 3).

  GET        /api/listings/{id}/calendar/?from&days     S10 strip, S11 date strip, S15 step 2 (owner)
  GET        /api/listings/{id}/start-times/?option_id&date&quantity   S11 start-time chips
  PUT/DELETE /api/listings/{id}/days/{date}/            S15 step 2: close one date / change its hours / undo
  POST       /api/bookings/quote/                        check a time + the estimate; holds nothing
  POST       /api/bookings/                              S11 "बुकिंग भेजें": the locked check, then the request
  GET        /api/bookings/?role=owner|renter&status=    S16 "मेरे पास आई बुकिंग" (+ its count for the badge
                                                         and the Home banner); the renter's list (slice 5)
  GET        /api/bookings/{id}/                         the renter or the owner of that booking (S12, S17, …)
  POST       /api/bookings/{id}/accept|reject|cancel|complete/   slice 4 (actions.py)

Renters see only hours. The owner also sees which booking holds each hour and which days they changed.
"""

from datetime import date, timedelta

from django.db import transaction
from django.db.models import Case, Exists, F, IntegerField, OuterRef, Q, Value, When
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from listings.models import DayOverride, Listing, ListingOption
from listings.serializers import WeeklyHoursSerializer
from core.pagination import Pages
from listings.views import my_listing

from . import actions
from . import requests as booking_requests
from .availability import Plan, bookings_outside, hours_for, load_plan, window
from .models import Booking
from .serializers import ActionSerializer, CreateSerializer, ListParams, QuoteSerializer, booking_json, ist, money


def visible_listing(pk) -> Listing:
    """Any listing a logged-in user may look at (not removed, owner not deleted), or 404."""
    qs = Listing.objects.select_related("machine_type").prefetch_related("weekly_hours")
    return get_object_or_404(
        qs.filter(owner__is_active=True).exclude(status=Listing.Status.REMOVED), pk=pk
    )


def in_window(day: date, now) -> date:
    first, last = window(now)
    if not first <= day <= last:
        raise ApiError("validation_error", fields={"date": "outside_30_days"})
    return day


def merged(spans: list[tuple[int, int]]) -> list[dict]:
    """Renters only need "which hours are taken": join touching and overlapping spans."""
    out: list[list[int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1][1] = max(out[-1][1], e)
        else:
            out.append([s, e])
    return [{"start_hour": s, "end_hour": e} for s, e in out]


def day_json(plan: Plan, day: date, *, owner: bool) -> dict:
    if not owner and not plan.listing.bookable:
        # Paused or no hours: renters see every day closed (S10 shows "मालिक ने अभी समय नहीं बताया").
        return {"date": day.isoformat(), "status": "closed", "open": None, "busy": []}
    open_ = plan.open_hours(day)
    busy = plan.busy(day)
    out = {
        "date": day.isoformat(),
        "status": plan.status(day),
        "open": {"start_hour": open_[0], "end_hour": open_[1]} if open_ else None,
    }
    if owner:
        out["busy"] = [
            {"start_hour": b.start_hour, "end_hour": b.end_hour, "booking_id": b.booking.pk,
             "status": b.booking.status}
            for b in busy
        ]
        out["override"] = day in plan.overrides
    else:
        out["busy"] = merged([(b.start_hour, b.end_hour) for b in busy])
    return out


# --------------------------------------------------------------------- calendar


class CalendarParams(serializers.Serializer):
    # "from" is a Python keyword, so the field is renamed in validate().
    days = serializers.IntegerField(min_value=1, max_value=30, default=30)

    def validate(self, attrs):
        raw = self.initial_data.get("from")
        try:
            attrs["first"] = date.fromisoformat(raw) if raw else None
        except ValueError:
            raise serializers.ValidationError({"from": "bad_date"})
        return attrs


class CalendarView(APIView):
    def get(self, request, pk):
        listing = visible_listing(pk)
        params = CalendarParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        now = timezone.now()
        first = in_window(params.validated_data["first"] or window(now)[0], now)
        last = first + timedelta(days=params.validated_data["days"] - 1)
        plan = load_plan(listing, first, last, now)
        owner = listing.owner_id == request.user.id
        return Response({
            "timezone": "Asia/Kolkata",
            "has_schedule": listing.has_schedule,
            "bookable": listing.bookable,
            "gap_hours": listing.gap_hours,
            "weekly_hours": [
                {"weekday": w, "start_hour": s, "end_hour": e} for w, (s, e) in sorted(plan.weekly.items())
            ],
            "days": [day_json(plan, first + timedelta(days=i), owner=owner)
                     for i in range((last - first).days + 1)],
        })


# --------------------------------------------------------------------- start times


class StartTimesParams(serializers.Serializer):
    option_id = serializers.IntegerField()
    date = serializers.DateField()
    quantity = serializers.DecimalField(max_digits=5, decimal_places=1)


class StartTimesView(APIView):
    def get(self, request, pk):
        listing = visible_listing(pk)
        params = StartTimesParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        p = params.validated_data
        option = ListingOption.objects.select_related("implement", "listing__machine_type").filter(
            listing=listing, is_active=True, pk=p["option_id"]
        ).first()
        if option is None:
            raise ApiError("validation_error", fields={"option_id": "unknown"})
        if not listing.bookable:
            raise ApiError("not_bookable", status.HTTP_409_CONFLICT)
        now = timezone.now()
        day = in_window(p["date"], now)
        hours = hours_for(option, p["quantity"])
        _, last = window(now)
        plan = load_plan(listing, day, last, now)

        if hours is None:  # per day: which dates (from `day`) are open with nothing booked
            free = [d for d in (day + timedelta(days=i) for i in range((last - day).days + 1)) if plan.day_free(d)]
            return Response({"hours": None, "days_free": [d.isoformat() for d in free]})

        starts = plan.start_hours(day, hours)
        next_free = None
        if not starts:  # suggest the next date that has a start time for the same job (S11)
            for i in range(1, (last - day).days + 1):
                if plan.start_hours(day + timedelta(days=i), hours):
                    next_free = (day + timedelta(days=i)).isoformat()
                    break
        return Response({"hours": hours, "start_hours": starts, "next_free_date": next_free})


# --------------------------------------------------------------------- one date (owner)


class DaySerializer(serializers.Serializer):
    is_closed = serializers.BooleanField()
    start_hour = serializers.IntegerField(min_value=0, max_value=23, required=False, allow_null=True)
    end_hour = serializers.IntegerField(min_value=1, max_value=24, required=False, allow_null=True)

    def validate(self, attrs):
        if attrs["is_closed"]:
            attrs["start_hour"] = attrs["end_hour"] = None
            return attrs
        hours = WeeklyHoursSerializer(data={"weekday": 0, **{k: attrs.get(k) for k in ("start_hour", "end_hour")}})
        hours.is_valid(raise_exception=True)
        return attrs


def parse_day(raw: str, now) -> date:
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        raise ApiError("validation_error", fields={"date": "bad_date"})
    return in_window(day, now)


def refuse_if_bookings(listing: Listing, now, day: date, new_open):
    """409 has_bookings when an active booking on `day` wouldn't fit `new_open` (None = closed)."""
    ids = bookings_outside(listing, now, lambda d: new_open if d == day else False)
    if ids:
        raise ApiError("has_bookings", status.HTTP_409_CONFLICT, fields={"booking_ids": ids})


class DayView(APIView):
    def put(self, request, pk, day):
        ser = DaySerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        now = timezone.now()
        the_day = parse_day(day, now)
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)  # same lock as booking creation (05 §5)
            if not listing.has_schedule:
                raise ApiError("not_bookable", status.HTTP_409_CONFLICT)
            refuse_if_bookings(listing, now, the_day, None if d["is_closed"] else (d["start_hour"], d["end_hour"]))
            DayOverride.objects.update_or_create(
                listing=listing, date=the_day,
                defaults={"is_closed": d["is_closed"], "start_hour": d["start_hour"], "end_hour": d["end_hour"]},
            )
        plan = load_plan(listing, the_day, the_day, now)
        return Response(day_json(plan, the_day, owner=True))

    def delete(self, request, pk, day):
        """Back to the weekly hours ("हर हफ़्ते वाला समय")."""
        now = timezone.now()
        the_day = parse_day(day, now)
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            weekly = {w.weekday: (w.start_hour, w.end_hour) for w in listing.weekly_hours.all()}
            refuse_if_bookings(listing, now, the_day, weekly.get(the_day.weekday()))
            DayOverride.objects.filter(listing=listing, date=the_day).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# --------------------------------------------------------------------- booking requests (slice 3)


class QuoteView(APIView):
    """Is this exact time still free, and roughly what will it cost? Holds nothing (06 §8)."""

    def post(self, request):
        ser = QuoteSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        option = booking_requests.find_option(d["option_id"], request.user)
        checked = booking_requests.check(option, d["start_at"], d["quantity"], timezone.now())
        last = checked.slots[-1].end_at
        return Response({
            "available": True,
            "hours": checked.hours,
            "start_at": ist(checked.slots[0].start_at),
            "end_at": ist(last),
            "blocked_until": ist(last + checked.gap),
            "days": len(checked.slots) if checked.hours is None else None,
            "estimated_total": money(checked.total()),
            "price_unit": option.price_unit,
        })


class BookingsView(APIView):
    throttle_scope = "bookings"

    def get_throttles(self):  # the throttle is for sending requests, not for reading the list
        return super().get_throttles() if self.request.method == "POST" else []

    def get(self, request):
        params = ListParams(data=request.query_params)
        params.is_valid(raise_exception=True)
        p = params.validated_data
        mine = Q(listing__owner=request.user) if p["role"] == "owner" else Q(renter=request.user)
        now = timezone.now()
        actions.expire_overdue(Booking.objects.filter(mine), now)  # lazy expiry: the list never shows a dead request
        qs = Booking.objects.filter(mine).exclude(  # a per-day group shows once, as its first day
            Exists(Booking.objects.filter(group_id=OuterRef("group_id"), start_at__lt=OuterRef("start_at")))
        )
        if p["status"]:
            qs = qs.filter(status__in=p["status"])
        qs = (
            qs.annotate(
                waiting=Case(When(status=Booking.Status.REQUESTED, then=Value(0)), default=Value(1),
                             output_field=IntegerField()),
                # Requests to answer: the one whose answer time ends first on top (emulator, slice 4: a
                # newer request for Saturday sat above tomorrow's, which had only 2 hours left).
                answer_by=Case(When(status=Booking.Status.REQUESTED, then=F("expires_at")), default=None),
            )
            .select_related("listing__machine_type", "listing__owner", "option__implement",
                            "renter__home_address", "job")
            .prefetch_related("events", "listing__photos")
            .order_by("waiting", "answer_by", "-created_at", "-id")  # 06 §8 + soonest answer time first
        )
        pages = Pages()
        page = pages.paginate_queryset(qs, request, view=self)
        groups: dict = {}
        for g in Booking.objects.filter(group_id__in={b.group_id for b in page if b.group_id}):
            groups.setdefault(g.group_id, []).append(g)
        return pages.get_paginated_response(
            [booking_json(b, group=groups.get(b.group_id) if b.group_id else None) for b in page]
        )

    def post(self, request):
        ser = CreateSerializer(data=request.data, context={"request": request})
        ser.is_valid(raise_exception=True)
        d = ser.validated_data
        option = booking_requests.find_option(d["option_id"], request.user)
        rows = booking_requests.create(
            renter=request.user, option=option, start_at=d["start_at"], quantity=d["quantity"],
            job=d["job"], job_other=d["job_other"], field=d["field"],
        )
        # The push to the owner ("booking_requested") comes with notifications (slices 6–7).
        return Response(booking_json(full_booking(rows[0].pk), group=rows), status=status.HTTP_201_CREATED)


def full_booking(pk) -> Booking:
    return Booking.objects.select_related(
        "listing__machine_type", "listing__owner", "option__implement", "renter__home_address", "job"
    ).prefetch_related("events").get(pk=pk)


class BookingDetailView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(
            Booking.objects.filter(Q(renter=request.user) | Q(listing__owner=request.user)), pk=pk
        )  # anyone else: 404, so ids can't be probed (06 §1)
        actions.expire_overdue(Booking.objects.filter(listing_id=b.listing_id), timezone.now())
        b = full_booking(b.pk)
        group = list(Booking.objects.filter(group_id=b.group_id)) if b.group_id else None
        return Response(booking_json(b, group=group))


class BookingActionView(APIView):
    """POST /api/bookings/{id}/accept|reject|cancel|complete/ → the updated booking (06 §8, actions.py)."""

    def post(self, request, pk, action):
        if action not in actions.ACTIONS:
            raise ApiError("not_found", status.HTTP_404_NOT_FOUND)
        ser = ActionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        b = actions.act(booking_id=pk, user=request.user, action=action, **ser.validated_data)
        b = full_booking(b.pk)
        group = list(Booking.objects.filter(group_id=b.group_id)) if b.group_id else None
        return Response(booking_json(b, group=group))
