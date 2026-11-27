"""Pushes (Phase 5 slice 7; 07 §4, 06 §9). Firebase is never called: `sent` replaces push._send_each.

Rules checked here:
- a push goes out only AFTER the booking change is saved (nothing for a refused change);
- the text = the approved glossary §8 sentence (the same cases as the app: push_text_cases.json);
- only booking_requested is high priority;
- a dead token is deleted; a failing Firebase never breaks the booking;
- a phone's token belongs to one person at a time, and logout forgets it (BUGS.md #1).
"""

import json
from datetime import datetime
from pathlib import Path

import pytest
from firebase_admin import exceptions as fb_errors
from firebase_admin import messaging
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from bookings.availability import at
from bookings.models import Booking
from notifications import push, push_text
from notifications.models import DeviceToken, Notification

from .conftest import TUE, book

S = Booking.Status
CASES = json.loads((Path(__file__).parent / "push_text_cases.json").read_text(encoding="utf-8"))["cases"]


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def sent(monkeypatch):
    """Every message 'sent', in order. Set sent.answer = fn(message) → None | Exception to fake Firebase."""
    class Sent(list):
        answer = staticmethod(lambda m: None)

    out = Sent()

    def fake(messages):
        out.extend(messages)
        return [out.answer(m) for m in messages]

    monkeypatch.setattr(push, "_send_each", fake)
    return out


def phone(user, token):
    return DeviceToken.objects.create(user=user, token=token)


def request_body(tractor, start=8, hours=3):
    return {"option_id": tractor.opt_cult.pk, "start_at": at(TUE, start).isoformat(), "hours": hours,
            "field": {"lat": "25.51", "lng": "81.86", "village": "सोरांव"}}


# --------------------------------------------------------------------- the words


@pytest.mark.parametrize("case", CASES, ids=[f"{i}-{c['type']}" for i, c in enumerate(CASES)])
def test_push_text_is_the_apps_sentence(case):
    assert push_text.text(case["type"], case["params"], datetime.fromisoformat(case["now"])) == case["text"]


def test_every_notification_type_has_a_text():
    assert set(push_text.TEXTS) == set(Notification.Type.values)


# --------------------------------------------------------------------- sending


class TestSending:
    def test_a_request_pushes_to_the_owners_phones_after_saving(
        self, tractor, owner, renter, sent, django_capture_on_commit_callbacks
    ):
        phone(owner, "owner-phone-1")
        phone(owner, "owner-phone-2")
        phone(renter, "renter-phone")
        with django_capture_on_commit_callbacks(execute=False) as later:
            res = client(renter).post("/api/bookings/", request_body(tractor), format="json")
        assert res.status_code == 201, res.json()
        assert sent == []  # nothing before the booking is really saved
        for fn in later:
            fn()
        assert sorted(m.fid for m in sent) == ["owner-phone-1", "owner-phone-2"]
        m = sent[0]
        assert m.notification.title == "खेती साधन"
        assert m.notification.body == (
            "Suresh आपकी मशीन (ट्रैक्टर + कल्टीवेटर) कल सुबह 8 बजे से सुबह 11 बजे तक के लिए बुक करना चाहते हैं। "
            "हाँ या ना बताएँ।"
        )
        n = Notification.objects.get(user=owner)
        assert m.data == {"type": "booking_requested", "role": "owner", "booking_id": str(res.json()["id"]),
                          "notification_id": str(n.pk)}
        assert m.android.priority == "high"
        assert m.android.notification.channel_id == "bookings"
        assert m.android.notification.tag == f"booking-{res.json()['id']}"

    def test_accepting_pushes_to_the_renter_at_normal_priority(
        self, tractor, owner, renter, sent, django_capture_on_commit_callbacks
    ):
        phone(renter, "renter-phone")
        phone(owner, "owner-phone")
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=S.REQUESTED, expires_at=at(TUE, 6))
        with django_capture_on_commit_callbacks(execute=True):
            res = client(owner).post(f"/api/bookings/{b.pk}/accept/", {}, format="json")
        assert res.status_code == 200, res.json()
        assert [m.fid for m in sent] == ["renter-phone"]  # never the owner who tapped
        assert sent[0].notification.body == "Ramesh ने आपकी बुकिंग पक्की कर दी। अब फ़ोन करें।"
        assert sent[0].data["role"] == "renter"
        assert sent[0].android.priority == "normal"

    def test_a_refused_request_pushes_nothing(self, tractor, owner, renter, sent, django_capture_on_commit_callbacks):
        phone(owner, "owner-phone")
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)  # that time is taken
        with django_capture_on_commit_callbacks(execute=True):
            res = client(renter).post("/api/bookings/", request_body(tractor, start=9, hours=2), format="json")
        assert res.status_code == 409
        assert sent == []

    def test_no_phone_no_push(self, tractor, owner, renter, sent, django_capture_on_commit_callbacks):
        with django_capture_on_commit_callbacks(execute=True):
            assert client(renter).post("/api/bookings/", request_body(tractor), format="json").status_code == 201
        assert sent == []
        assert Notification.objects.filter(user=owner).count() == 1  # the bell still has it

    def test_a_dead_token_is_deleted_the_others_kept(
        self, tractor, owner, renter, sent, django_capture_on_commit_callbacks
    ):
        phone(owner, "uninstalled")
        phone(owner, "wrong-project")
        phone(owner, "busy")
        phone(owner, "fine")
        errors = {
            "uninstalled": messaging.UnregisteredError("gone"),
            "wrong-project": messaging.SenderIdMismatchError("other project"),
            "busy": fb_errors.UnavailableError("try later"),  # temporary: keep the token
        }
        sent.answer = lambda m: errors.get(m.fid)
        with django_capture_on_commit_callbacks(execute=True):
            assert client(renter).post("/api/bookings/", request_body(tractor), format="json").status_code == 201
        assert len(sent) == 4
        assert sorted(DeviceToken.objects.values_list("token", flat=True)) == ["busy", "fine"]

    def test_firebase_down_never_breaks_the_booking(
        self, tractor, owner, renter, monkeypatch, django_capture_on_commit_callbacks
    ):
        phone(owner, "owner-phone")

        def boom(messages):
            raise fb_errors.UnavailableError("Firebase is down")

        monkeypatch.setattr(push, "_send_each", boom)
        with django_capture_on_commit_callbacks(execute=True):
            res = client(renter).post("/api/bookings/", request_body(tractor), format="json")
        assert res.status_code == 201
        assert Booking.objects.filter(pk=res.json()["id"], status=S.REQUESTED).exists()
        assert Notification.objects.filter(user=owner).count() == 1
        assert DeviceToken.objects.filter(token="owner-phone").exists()

    def test_expiry_reaches_both_phones(self, tractor, owner, renter, sent, frozen, django_capture_on_commit_callbacks):
        phone(owner, "owner-phone")
        phone(renter, "renter-phone")
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11, status=S.REQUESTED, expires_at=at(TUE, 6))
        frozen.move_to("2026-11-24 01:00:00")  # 06:30 IST: the answer time is over
        with django_capture_on_commit_callbacks(execute=True):
            client(renter).get("/api/notifications/unread-count/")
        assert {(m.fid, m.notification.body) for m in sent} == {
            ("renter-phone", "मालिक ने समय पर जवाब नहीं दिया, बुकिंग रद्द हो गई।"),
            ("owner-phone", "आपने समय पर जवाब नहीं दिया, Suresh की बुकिंग रद्द हो गई।"),
        }

    def test_without_a_key_pushes_are_off_quietly(self, settings, tractor, owner, renter, monkeypatch):
        settings.FIREBASE_CREDENTIALS_PATH = ""
        monkeypatch.setattr(push, "_app", None)
        phone(owner, "owner-phone")
        n = Notification.objects.create(user=owner, type="booking_accepted", role="owner", params={"owner": "R"})
        assert push.send([n]) == 1  # "sent" to nobody, no error
        assert DeviceToken.objects.filter(token="owner-phone").exists()


# --------------------------------------------------------------------- the phone's address


class TestDevices:
    def test_register_then_again(self, owner):
        c = client(owner)
        assert c.post("/api/devices/", {"token": "abc:123", "platform": "android"}, format="json").status_code == 201
        assert c.post("/api/devices/", {"token": "abc:123"}, format="json").status_code == 200
        assert list(DeviceToken.objects.values_list("user_id", "token")) == [(owner.pk, "abc:123")]

    def test_needs_login_and_a_token(self, owner):
        assert APIClient().post("/api/devices/", {"token": "t"}, format="json").status_code == 401
        res = client(owner).post("/api/devices/", {}, format="json")
        assert res.status_code == 400
        assert "token" in res.json()["error"]["fields"]
        res = client(owner).post("/api/devices/", {"token": "t", "platform": "ios"}, format="json")
        assert res.status_code == 400

    def test_another_person_on_the_same_phone_takes_the_token(self, owner, renter):
        """BUGS.md #1 for pushes: Ramesh logs out, Suresh logs in on the same phone → only Suresh's pushes."""
        phone(owner, "shared-phone")
        assert client(renter).post("/api/devices/", {"token": "shared-phone"}, format="json").status_code == 200
        assert list(DeviceToken.objects.values_list("user_id", flat=True)) == [renter.pk]

    def test_only_the_newest_few_are_kept(self, owner):
        for i in range(DeviceToken.MAX_PER_USER + 2):
            client(owner).post("/api/devices/", {"token": f"t{i}"}, format="json")
        kept = set(DeviceToken.objects.values_list("token", flat=True))
        assert len(kept) == DeviceToken.MAX_PER_USER
        assert {"t0", "t1"}.isdisjoint(kept)

    def test_delete_only_my_own(self, owner, renter):
        phone(owner, "owners")
        phone(renter, "renters")
        assert client(owner).delete("/api/devices/renters/").status_code == 204
        assert client(owner).delete("/api/devices/owners/").status_code == 204
        assert client(owner).delete("/api/devices/never-seen/").status_code == 204
        assert list(DeviceToken.objects.values_list("token", flat=True)) == ["renters"]

    def test_logout_forgets_this_phones_token(self, owner, renter):
        phone(owner, "this-phone")
        phone(owner, "other-phone")
        phone(renter, "someone-else")
        refresh = str(RefreshToken.for_user(owner))
        res = APIClient().post("/api/auth/logout/", {"refresh": refresh, "device_token": "this-phone"}, format="json")
        assert res.status_code == 204
        assert sorted(DeviceToken.objects.values_list("token", flat=True)) == ["other-phone", "someone-else"]

    def test_logout_cannot_remove_another_persons_token(self, owner, renter):
        phone(renter, "renters-phone")
        refresh = str(RefreshToken.for_user(owner))
        APIClient().post("/api/auth/logout/", {"refresh": refresh, "device_token": "renters-phone"}, format="json")
        assert DeviceToken.objects.filter(token="renters-phone").exists()

    def test_logout_without_a_device_token_still_works(self, owner):
        refresh = str(RefreshToken.for_user(owner))
        assert APIClient().post("/api/auth/logout/", {"refresh": refresh}, format="json").status_code == 204
