from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenRefreshView

from core.errors import ApiError
from notifications.models import DeviceToken

from . import firebase
from .models import Address, User
from .phone import is_indian_mobile
from .serializers import (
    AddressSerializer,
    FirebaseLoginSerializer,
    LogoutSerializer,
    MeUpdateSerializer,
    UserSerializer,
)


def _tokens_for(user: User) -> dict:
    refresh = RefreshToken.for_user(user)
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


class FirebaseLoginView(APIView):
    """
    POST /api/auth/firebase/   (public)
    The phone proved its number to Firebase with an OTP. We check Firebase's
    token and hand out Agriverse's own login tokens. See docs/06-api.md §2.
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []  # an old/expired access token must not block logging in
    throttle_scope = "auth_firebase"

    def post(self, request):
        ser = FirebaseLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        try:
            claims = firebase.verify_id_token(ser.validated_data["firebase_id_token"])
        except firebase.FirebaseUnavailable:
            raise ApiError("auth_unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)
        except firebase.InvalidFirebaseToken:
            raise ApiError("invalid_token", status.HTTP_401_UNAUTHORIZED)

        uid = claims.get("uid") or claims.get("sub")
        phone = claims.get("phone_number")
        if not uid:
            raise ApiError("invalid_token", status.HTTP_401_UNAUTHORIZED)
        if not is_indian_mobile(phone):
            raise ApiError("phone_not_supported", status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            user = User.objects.select_for_update().filter(firebase_uid=uid).first()
            if user is None:
                # Same phone, new Firebase account (e.g. Firebase user was recreated): keep the person.
                user = User.objects.select_for_update().filter(phone=phone).first()
                if user is not None:
                    user.firebase_uid = uid
                    user.save(update_fields=["firebase_uid"])
            created = user is None
            if created:
                user = User.objects.create_user(phone=phone, firebase_uid=uid)
            elif user.phone != phone:
                # The Firebase account's number changed; keep ours in step,
                # unless another Agriverse account already uses that number.
                if User.objects.filter(phone=phone).exclude(pk=user.pk).exists():
                    raise ApiError("phone_in_use", status.HTTP_409_CONFLICT)
                user.phone = phone
                user.save(update_fields=["phone"])

        if not user.is_active:
            raise ApiError("account_disabled", status.HTTP_403_FORBIDDEN)

        return Response(
            {
                **_tokens_for(user),
                # Sign-up isn't finished without a name (S4) and a home address (S5/S6),
                # so the app continues there.
                "is_new_user": created or not user.name or user.home_address_id is None,
                "user": UserSerializer(user).data,
            }
        )


class RefreshView(TokenRefreshView):
    """
    POST /api/auth/token/refresh/   (public, needs a refresh token)
    Returns a NEW access + refresh pair; the old refresh token stops working.
    """

    throttle_scope = "token_refresh"
    authentication_classes = []


class LogoutView(APIView):
    """
    POST /api/auth/logout/   {"refresh": "...", "device_token"?: "..."}  → 204
    Kills this phone's session on the server. Works even if the access token
    has already expired, so the farmer can always log out.
    device_token (slice 7): this phone's push address is deleted too, but only when the refresh token is
    still valid and belongs to the token's owner (a stranger can't remove someone else's).
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ser = LogoutSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            refresh = RefreshToken(ser.validated_data["refresh"])
            device = ser.validated_data.get("device_token")
            if device:
                DeviceToken.objects.filter(token=device, user_id=refresh.payload.get("user_id")).delete()
            refresh.blacklist()
        except TokenError:
            pass  # already expired or blacklisted: the session is dead either way
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(APIView):
    """
    GET   /api/me/ → the logged-in user.
    PATCH /api/me/ {name?, photo_url?} → the updated user (S4 name + photo).
    """

    def get(self, request):
        return Response(UserSerializer(request.user).data)

    def patch(self, request):
        user = request.user
        ser = MeUpdateSerializer(data=request.data, context={"user": user})
        ser.is_valid(raise_exception=True)
        changed = list(ser.validated_data)
        for field, value in ser.validated_data.items():
            setattr(user, field, value)
        if changed:
            user.save(update_fields=changed)
        return Response(UserSerializer(user).data)


class AddressListView(APIView):
    """
    GET  /api/me/addresses/ → my saved places (not deleted), oldest first.
    POST /api/me/addresses/ → save a place (S6). The first one, or make_home=true,
         becomes the home address. At most 20 per farmer.
    Edit/delete come with Profile (S20).
    """

    def get(self, request):
        rows = Address.objects.filter(user=request.user, is_deleted=False)
        return Response(AddressSerializer(rows, many=True).data)

    def post(self, request):
        ser = AddressSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        make_home = ser.validated_data.pop("make_home", False)
        with transaction.atomic():
            user = User.objects.select_for_update().get(pk=request.user.pk)
            if Address.objects.filter(user=user, is_deleted=False).count() >= Address.MAX_PER_USER:
                raise ApiError("too_many_addresses", status.HTTP_400_BAD_REQUEST)
            address = ser.save(user=user)
            if make_home or user.home_address_id is None:
                user.home_address = address
                user.save(update_fields=["home_address"])
        return Response(AddressSerializer(address).data, status=status.HTTP_201_CREATED)


class AddressDetailView(APIView):
    """
    PATCH /api/me/addresses/{id}/ → change one of MY places (Profile: "बदलें" on the home address).
    The same row is edited, so changing the home again and again never hits the 20-places limit.
    Someone else's, or a deleted one, is "not found". (DELETE comes with the full S20.)
    """

    def patch(self, request, pk):
        address = Address.objects.filter(pk=pk, user=request.user, is_deleted=False).first()
        if address is None:
            raise ApiError("not_found", status.HTTP_404_NOT_FOUND)
        ser = AddressSerializer(address, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.validated_data.pop("make_home", None)  # this one is already where it is; only its place changes
        ser.save()
        return Response(AddressSerializer(address).data)


class AppConfigView(APIView):
    """GET /api/app-config/ (public). Also used by the app as a 'can I reach the server?' check."""

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def get(self, request):
        return Response(
            {
                "min_version": "1.0.0",
                "latest_version": "1.0.0",
                "support_phone": "",
                "booking_expiry_minutes": 120,
            }
        )
