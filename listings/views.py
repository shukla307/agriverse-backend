"""
Owner-side listing endpoints (docs/06-api.md §5) and the weekly schedule (§6).
Someone else's listing always answers 404, so ids can't be probed (06 §1).
Search for renters is SearchView (GET /api/search/, listings/search.py). Public details: Phase 4 slice 3.
"""

from dataclasses import replace

from django.db import transaction
from django.db.models import Prefetch
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.errors import ApiError
from core.pagination import Pages

from .models import Listing, ListingOption, Photo, WeeklyHours
from .search import SearchParams, exists_anywhere, resolve, search_queryset
from .serializers import (
    FromPointSerializer,
    ListingCreateSerializer,
    ListingMineSerializer,
    ListingOwnerSerializer,
    ListingPublicSerializer,
    ListingUpdateSerializer,
    OptionReadSerializer,
    OptionWriteSerializer,
    PhotoOrderSerializer,
    PhotoReadSerializer,
    PhotoWriteSerializer,
    SearchParamsSerializer,
    SearchResultSerializer,
    WeekSerializer,
    option_key,
)


def full_listing_queryset():
    return Listing.objects.select_related("machine_type").prefetch_related(
        Prefetch("options", queryset=ListingOption.objects.select_related("implement")),
        "photos",
        "weekly_hours",
    )


def my_listing(request, pk, *, lock=False) -> Listing:
    """My listing (not removed), or 404."""
    qs = Listing.objects.select_related("machine_type").filter(owner=request.user).exclude(
        status=Listing.Status.REMOVED
    )
    if lock:
        qs = qs.select_for_update(of=("self",))
    return get_object_or_404(qs, pk=pk)


def owner_view(listing_id) -> dict:
    return ListingOwnerSerializer(full_listing_queryset().get(pk=listing_id)).data


def save_week(listing: Listing, days: list[dict]):
    """Replace the whole weekly schedule. Booking clashes (409 has_bookings) are checked in Phase 5."""
    WeeklyHours.objects.filter(listing=listing).delete()
    WeeklyHours.objects.bulk_create(WeeklyHours(listing=listing, **d) for d in days)
    listing.has_schedule = bool(days)
    listing.save(update_fields=["has_schedule", "updated_at"])


class ListingCreateView(APIView):
    """POST /api/listings/"""

    def post(self, request):
        ser = ListingCreateSerializer(
            data=request.data, context={"user": request.user, "firebase_uid": request.user.firebase_uid}
        )
        ser.is_valid(raise_exception=True)
        data = dict(ser.validated_data)
        options, photos, week = data.pop("options"), data.pop("photos", []), data.pop("weekly_hours")
        with transaction.atomic():
            listing = Listing.objects.create(owner=request.user, **data)
            ListingOption.objects.bulk_create(ListingOption(listing=listing, **o) for o in options)
            Photo.objects.bulk_create(Photo(listing=listing, sort_order=i, **p) for i, p in enumerate(photos))
            if week:
                save_week(listing, week)
        return Response(owner_view(listing.pk), status=status.HTTP_201_CREATED)


class ListingMineView(APIView):
    """GET /api/listings/mine/: my machines (active and paused), newest first, 20 per page."""

    def get(self, request):
        qs = (
            Listing.objects.filter(owner=request.user)
            .exclude(status=Listing.Status.REMOVED)
            .select_related("machine_type")
            .prefetch_related("photos", "options")
            .order_by("-created_at", "-id")
        )
        pages = Pages()
        page = pages.paginate_queryset(qs, request, view=self)
        return pages.get_paginated_response(ListingMineSerializer(page, many=True).data)


class ListingDetailView(APIView):
    """GET /api/listings/{id}/: the owner gets the full owner view (S21 edit); anyone else the renter view (S10).
    PATCH / DELETE: owner only (someone else's id → 404)."""

    def get(self, request, pk):
        point = FromPointSerializer(data=request.query_params)
        point.is_valid(raise_exception=True)
        listing = get_object_or_404(
            full_listing_queryset().select_related("owner__home_address").filter(owner__is_active=True)
            # Paused still opens (with its status), so a card tapped just before the owner paused it
            # doesn't vanish. Removed = gone.
            .exclude(status=Listing.Status.REMOVED),
            pk=pk,
        )
        if listing.owner_id == request.user.id:
            return Response(owner_view(listing.pk))
        p = point.validated_data
        context = {"from_point": (p["lat"], p["lng"]) if p else None}
        return Response(ListingPublicSerializer(listing, context=context).data)

    def patch(self, request, pk):
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            ser = ListingUpdateSerializer(
                data=request.data, partial=True, context={"listing": listing, "user": request.user}
            )
            ser.is_valid(raise_exception=True)
            for field, value in ser.validated_data.items():
                setattr(listing, field, value)
            listing.save()
        return Response(owner_view(listing.pk))

    def delete(self, request, pk):
        # Phase 5: refuse with 409 invalid_status while future accepted bookings exist.
        listing = my_listing(request, pk)
        listing.status = Listing.Status.REMOVED
        listing.save(update_fields=["status", "updated_at"])
        return Response(status=status.HTTP_204_NO_CONTENT)


def check_not_duplicate(listing: Listing, data: dict, *, except_id=None):
    others = listing.options.filter(is_active=True).exclude(pk=except_id)
    if any(option_key(o) == option_key(data) for o in others):
        raise ApiError("validation_error", fields={"options": "duplicate_option"})


class OptionListView(APIView):
    """POST /api/listings/{id}/options/"""

    def post(self, request, pk):
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            ser = OptionWriteSerializer(data=request.data, context={"machine_type": listing.machine_type})
            ser.is_valid(raise_exception=True)
            check_not_duplicate(listing, ser.validated_data)
            if listing.options.filter(is_active=True).count() >= ListingOption.MAX_PER_LISTING:
                raise ApiError("validation_error", fields={"options": "too_many_options"})
            option = ListingOption.objects.create(listing=listing, **ser.validated_data)
        return Response(OptionReadSerializer(option).data, status=status.HTTP_201_CREATED)


class OptionDetailView(APIView):
    """PATCH / DELETE /api/listings/{id}/options/{opt_id}/"""

    def _option(self, listing, opt_id) -> ListingOption:
        return get_object_or_404(listing.options.select_related("implement"), pk=opt_id, is_active=True)

    def patch(self, request, pk, opt_id):
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            option = self._option(listing, opt_id)
            # PATCH sends only what changed; check the result as a whole option.
            current = {
                "implement_id": option.implement_id,
                "implement_other": option.implement_other,
                "price_unit": option.price_unit,
                "price": option.price,
                "min_hours": option.min_hours,
                "hours_per_acre": option.hours_per_acre,
            }
            incoming = request.data if isinstance(request.data, dict) else {}
            if "price_unit" in incoming and incoming["price_unit"] != option.price_unit:
                # A new unit: the old unit's extras don't carry over.
                current.update(min_hours=1, hours_per_acre=None, price=None)
            ser = OptionWriteSerializer(data={**current, **incoming}, context={"machine_type": listing.machine_type})
            ser.is_valid(raise_exception=True)
            check_not_duplicate(listing, ser.validated_data, except_id=option.pk)
            # Bookings keep their own price snapshot (Phase 5), so editing only affects new bookings.
            for field, value in ser.validated_data.items():
                setattr(option, field, value)
            option.save()
        return Response(OptionReadSerializer(option).data)

    def delete(self, request, pk, opt_id):
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            option = self._option(listing, opt_id)
            if listing.options.filter(is_active=True).count() <= 1:
                raise ApiError("validation_error", fields={"options": "at_least_one"})
            option.is_active = False
            option.save(update_fields=["is_active"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class PhotoListView(APIView):
    """POST /api/listings/{id}/photos/: max 5 (decision §17)."""

    throttle_scope = "photos"

    def post(self, request, pk):
        ser = PhotoWriteSerializer(data=request.data, context={"firebase_uid": request.user.firebase_uid})
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            count = listing.photos.count()
            if count >= Listing.MAX_PHOTOS:
                raise ApiError("too_many_photos")
            photo = Photo.objects.create(listing=listing, sort_order=count, **ser.validated_data)
        return Response(PhotoReadSerializer(photo).data, status=status.HTTP_201_CREATED)


class PhotoOrderView(APIView):
    """PATCH /api/listings/{id}/photos/order/ {"photo_ids": [3, 1, 2]}: the first becomes the cover."""

    def patch(self, request, pk):
        ser = PhotoOrderSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        ids = ser.validated_data["photo_ids"]
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            photos = {p.pk: p for p in listing.photos.all()}
            if sorted(ids) != sorted(photos):
                raise ApiError("validation_error", fields={"photo_ids": "must_list_every_photo_once"})
            for i, pid in enumerate(ids):
                photos[pid].sort_order = i
            Photo.objects.bulk_update(photos.values(), ["sort_order"])
        return Response(PhotoReadSerializer(sorted(photos.values(), key=lambda p: p.sort_order), many=True).data)


class PhotoDetailView(APIView):
    """
    DELETE /api/listings/{id}/photos/{photo_id}/: removes the row.
    The APP deletes the file in Storage (the rules let owners delete their own files),
    so the server needs no secret Firebase key (owner decision 2026-11-19).
    """

    def delete(self, request, pk, photo_id):
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            photo = get_object_or_404(listing.photos, pk=photo_id)
            photo.delete()
            rest = list(listing.photos.all())
            for i, p in enumerate(rest):
                p.sort_order = i
            Photo.objects.bulk_update(rest, ["sort_order"])
        return Response(status=status.HTTP_204_NO_CONTENT)


class WeeklyHoursView(APIView):
    """PUT /api/listings/{id}/weekly-hours/: replaces the whole week. At least one day."""

    def put(self, request, pk):
        ser = WeekSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        with transaction.atomic():
            listing = my_listing(request, pk, lock=True)
            save_week(listing, ser.validated_data["days"])
        days = [{"weekday": w.weekday, "start_hour": w.start_hour, "end_hour": w.end_hour}
                for w in listing.weekly_hours.all()]
        return Response({"has_schedule": True, "days": days})


class SearchView(APIView):
    """GET /api/search/?lat&lng[&radius_km&job&machine&q&unit&hp_min&hp_max&page]: machines inside the radius,
    nearest first, 20 per page. count == 0 → hint "none_in_radius", "no_match_filters" or "no_match"."""

    def get(self, request):
        ser = SearchParamsSerializer(data=request.query_params)
        ser.is_valid(raise_exception=True)
        asked = SearchParams(**ser.validated_data)
        params = resolve(asked)
        pages = Pages()
        page = pages.paginate_queryset(search_queryset(params, exclude_owner=request.user), request, view=self)
        response = pages.get_paginated_response(
            SearchResultSerializer(page, many=True, context={"params": params}).data
        )
        response.data["hint"] = None
        if response.data["count"] == 0:
            # "none_in_radius": such machines exist, just farther away → the app offers a bigger circle.
            # "no_match": nothing like this anywhere (e.g. nonsense typed) → a bigger circle wouldn't help.
            # "no_match_filters": nothing anywhere WITH the job chip / unit / HP filters, but there is without
            # them → the app offers "फ़िल्टर हटाएँ" (and not when the typed name is the problem). The job counts
            # as a filter: "drone" with कटाई still chosen found nothing, but drones exist (owner, 2026-11-21).
            filtered = asked.job or asked.unit or asked.hp_min is not None or asked.hp_max is not None
            if exists_anywhere(params, exclude_owner=request.user):
                response.data["hint"] = "none_in_radius"
            elif filtered and exists_anywhere(
                resolve(replace(asked, job=None, unit=None, hp_min=None, hp_max=None)), exclude_owner=request.user
            ):
                response.data["hint"] = "no_match_filters"
            else:
                response.data["hint"] = "no_match"
        return response
