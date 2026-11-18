"""PATCH /api/me/: S4 name + photo (docs/06-api.md §3)."""

import pytest
from django.test import override_settings

from accounts.models import User

pytestmark = pytest.mark.django_db

UID = "uid-abc"
OWN_PHOTO = (
    "https://firebasestorage.googleapis.com/v0/b/agriverse-main.firebasestorage.app/o/"
    f"profiles%2F{UID}%2Favatar.jpg?alt=media&token=abc-123"
)


@pytest.fixture
def user():
    return User.objects.create_user(phone="+919876543210", firebase_uid=UID)


@pytest.fixture
def me(api, user):
    api.force_authenticate(user)
    return api


def patch(api, body):
    return api.patch("/api/me/", body, format="json")


def test_name_is_saved_and_tidied(me, user):
    res = patch(me, {"name": "  रमेश    कुमार \n"})
    assert res.status_code == 200
    assert res.json()["name"] == "रमेश कुमार"
    user.refresh_from_db()
    assert user.name == "रमेश कुमार"


@pytest.mark.parametrize("name", ["", " ", "a", "  b  ", "x" * 81])
def test_bad_names_are_refused_with_field_error(me, name):
    res = patch(me, {"name": name})
    assert res.status_code == 400
    body = res.json()["error"]
    assert body["code"] == "validation_error"
    assert "name" in body["fields"]


def test_own_photo_link_is_saved(me, user):
    res = patch(me, {"name": "Ramesh", "photo_url": OWN_PHOTO})
    assert res.status_code == 200
    user.refresh_from_db()
    assert user.photo_url == OWN_PHOTO


@pytest.mark.parametrize(
    "url",
    [
        OWN_PHOTO.replace(UID, "someone-else"),  # another person's photo
        OWN_PHOTO.replace("agriverse-main.firebasestorage.app", "other-bucket.appspot.com"),  # other bucket
        OWN_PHOTO.replace("avatar.jpg", "x.jpg"),
        OWN_PHOTO.replace("https://", "http://"),
        "https://evil.example.com/v0/b/agriverse-main.firebasestorage.app/o/profiles%2Fuid-abc%2Favatar.jpg",
        "not a url",
    ],
)
def test_foreign_photo_links_are_refused(me, url):
    res = patch(me, {"photo_url": url})
    assert res.status_code == 400
    assert "photo_url" in res.json()["error"]["fields"]


def test_empty_photo_removes_it(me, user):
    user.photo_url = OWN_PHOTO
    user.save()
    assert patch(me, {"photo_url": ""}).status_code == 200
    user.refresh_from_db()
    assert user.photo_url == ""


@override_settings(FIREBASE_STORAGE_BUCKET="test-bucket")
def test_bucket_comes_from_settings(me):
    good = OWN_PHOTO.replace("agriverse-main.firebasestorage.app", "test-bucket")
    assert patch(me, {"photo_url": good}).status_code == 200
    assert patch(me, {"photo_url": OWN_PHOTO}).status_code == 400


def test_phone_cannot_be_changed_here(me, user):
    res = patch(me, {"phone": "+919999999999", "name": "Ramesh"})
    assert res.status_code == 200
    user.refresh_from_db()
    assert user.phone == "+919876543210"


def test_empty_patch_changes_nothing(me):
    assert patch(me, {}).status_code == 200


def test_login_required(api):
    res = patch(api, {"name": "Ramesh"})
    assert res.status_code == 401
    assert res.json()["error"]["code"] == "invalid_token"


def test_user_with_name_is_no_longer_new_at_next_login(me, user):
    patch(me, {"name": "Ramesh"})
    user.refresh_from_db()
    assert user.name
