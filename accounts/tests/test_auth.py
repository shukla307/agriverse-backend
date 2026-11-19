"""
Login, token refresh, logout and /me (docs/06-api.md §2–§3).
Firebase is replaced by a fake, so these tests never send an SMS or use the network.
"""

from unittest import mock

import pytest

from accounts import firebase
from accounts.models import Address, User

pytestmark = pytest.mark.django_db

PHONE = "+919876543210"
UID = "firebase-uid-123"


def fake_firebase(uid=UID, phone=PHONE):
    return mock.patch("accounts.firebase.verify_id_token", return_value={"uid": uid, "phone_number": phone})


def login(api, token="good-token"):
    return api.post("/api/auth/firebase/", {"firebase_id_token": token}, format="json")


# --- POST /api/auth/firebase/ ------------------------------------------------

def test_new_phone_creates_user_and_returns_tokens(api):
    with fake_firebase():
        res = login(api)
    assert res.status_code == 200
    body = res.json()
    assert body["access"] and body["refresh"]
    assert body["is_new_user"] is True
    assert body["user"] == {"id": body["user"]["id"], "phone": PHONE, "name": "", "photo_url": "", "home_address": None}
    user = User.objects.get(phone=PHONE)
    assert user.firebase_uid == UID
    assert not user.has_usable_password()  # farmers never get a password


def test_returning_user_with_name_and_home_is_not_new(api):
    user = User.objects.create_user(phone=PHONE, firebase_uid=UID, name="Ramesh")
    user.home_address = Address.objects.create(user=user, label="home", lat="25.438", lng="81.905")
    user.save()
    with fake_firebase():
        res = login(api)
    assert res.status_code == 200
    assert res.json()["is_new_user"] is False
    assert User.objects.count() == 1


def test_user_without_name_still_counts_as_new(api):
    """Signed up but closed the app before typing a name: continue at S4."""
    User.objects.create_user(phone=PHONE, firebase_uid=UID)
    with fake_firebase():
        assert login(api).json()["is_new_user"] is True


def test_user_with_name_but_no_home_address_still_counts_as_new(api):
    """Closed the app on S5/S6: continue sign-up."""
    User.objects.create_user(phone=PHONE, firebase_uid=UID, name="Ramesh")
    with fake_firebase():
        assert login(api).json()["is_new_user"] is True


def test_same_phone_new_firebase_account_keeps_the_same_user(api):
    old = User.objects.create_user(phone=PHONE, firebase_uid="old-uid", name="Ramesh")
    with fake_firebase(uid="new-uid"):
        res = login(api)
    assert res.json()["user"]["id"] == old.id
    old.refresh_from_db()
    assert old.firebase_uid == "new-uid"
    assert User.objects.count() == 1


def test_wrong_token_is_refused(api):
    with mock.patch("accounts.firebase.verify_id_token", side_effect=firebase.InvalidFirebaseToken("bad")):
        res = login(api)
    assert res.status_code == 401
    assert res.json() == {"error": {"code": "invalid_token", "fields": {}}}
    assert User.objects.count() == 0


def test_google_unreachable_is_not_blamed_on_the_user(api):
    with mock.patch("accounts.firebase.verify_id_token", side_effect=firebase.FirebaseUnavailable("down")):
        res = login(api)
    assert res.status_code == 503
    assert res.json()["error"]["code"] == "auth_unavailable"


@pytest.mark.parametrize("phone", ["+14155550100", "+915876543210", None])
def test_non_indian_or_missing_phone_is_refused(api, phone):
    with fake_firebase(phone=phone):
        res = login(api)
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "phone_not_supported"
    assert User.objects.count() == 0


def test_disabled_account_cannot_log_in(api):
    User.objects.create_user(phone=PHONE, firebase_uid=UID, name="X", is_active=False)
    with fake_firebase():
        res = login(api)
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "account_disabled"


def test_missing_token_gives_field_error(api):
    res = api.post("/api/auth/firebase/", {}, format="json")
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "validation_error"
    assert "firebase_id_token" in err["fields"]


def test_login_is_rate_limited(api):
    with fake_firebase():
        codes = [login(api).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10
    assert codes[10] == 429


def test_rate_limit_uses_error_format(api):
    with fake_firebase():
        for _ in range(10):
            login(api)
        res = login(api)
    assert res.json()["error"]["code"] == "rate_limited"


def test_real_firebase_check_works_without_a_secret_key_file():
    """The real verifier must reject junk on its own, without any service-account file."""
    with pytest.raises(firebase.InvalidFirebaseToken):
        firebase.verify_id_token("this.is.not-a-real-token")


# --- POST /api/auth/token/refresh/ -------------------------------------------

def _logged_in(api):
    with fake_firebase():
        return login(api).json()


def test_refresh_gives_a_new_pair_and_old_refresh_stops_working(api):
    first = _logged_in(api)
    res = api.post("/api/auth/token/refresh/", {"refresh": first["refresh"]}, format="json")
    assert res.status_code == 200
    second = res.json()
    assert second["access"] and second["refresh"] and second["refresh"] != first["refresh"]

    again = api.post("/api/auth/token/refresh/", {"refresh": first["refresh"]}, format="json")
    assert again.status_code == 401
    assert again.json()["error"]["code"] == "invalid_token"


def test_refresh_with_junk_is_refused(api):
    res = api.post("/api/auth/token/refresh/", {"refresh": "junk"}, format="json")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_token"


def test_refresh_refused_after_account_is_disabled(api):
    tokens = _logged_in(api)
    User.objects.filter(phone=PHONE).update(is_active=False)
    res = api.post("/api/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert res.status_code == 401


def test_session_lifetimes_match_the_decision():
    from django.conf import settings

    jwt = settings.SIMPLE_JWT
    assert jwt["ACCESS_TOKEN_LIFETIME"].total_seconds() == 30 * 60
    assert jwt["REFRESH_TOKEN_LIFETIME"].days == 365
    assert jwt["ROTATE_REFRESH_TOKENS"] and jwt["BLACKLIST_AFTER_ROTATION"]


# --- POST /api/auth/logout/ --------------------------------------------------

def test_logout_kills_the_session(api):
    tokens = _logged_in(api)
    res = api.post("/api/auth/logout/", {"refresh": tokens["refresh"]}, format="json")
    assert res.status_code == 204
    after = api.post("/api/auth/token/refresh/", {"refresh": tokens["refresh"]}, format="json")
    assert after.status_code == 401


def test_logout_with_an_already_dead_token_still_succeeds(api):
    assert api.post("/api/auth/logout/", {"refresh": "junk"}, format="json").status_code == 204


# --- GET /api/me/ and /api/app-config/ --------------------------------------

def test_me_with_token(api):
    tokens = _logged_in(api)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {tokens['access']}")
    res = api.get("/api/me/")
    assert res.status_code == 200
    assert res.json()["phone"] == PHONE


@pytest.mark.parametrize("header", [None, "Bearer junk"])
def test_me_without_valid_token_is_401_with_code(api, header):
    if header:
        api.credentials(HTTP_AUTHORIZATION=header)
    res = api.get("/api/me/")
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_token"


def test_app_config_is_public(api):
    res = api.get("/api/app-config/")
    assert res.status_code == 200
    assert res.json()["min_version"] == "1.0.0"


def test_old_endpoints_are_gone_on_v1(api):
    assert api.post("/api/auth/login/", {}, format="json").status_code == 404
    assert api.get("/api/equipment/search/").status_code == 404
