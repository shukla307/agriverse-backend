"""The bell (Phase 5 slice 6; 05 §3.11, 06 §9).

Who hears what: the OTHER person hears about a change, never the one who made it; a change by the system
(expiry, auto-complete) reaches both. Clock: Mon 2026-11-23 07:30 IST. Ramesh owns the tractor, Suresh rents.
"""

import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from bookings.availability import at
from bookings.models import Booking
from notifications.models import Notification

from .conftest import MON, TUE, WED, book

S = Booking.Status
LATER = at(TUE, 6)


def client(user):
    c = APIClient()
    c.force_authenticate(user)
    return c


def waiting(tractor, renter, day=TUE, start=8, end=11, expires_at=LATER):
    return book(tractor, tractor.opt_hour, renter, day, start, end, status=S.REQUESTED, expires_at=expires_at)


def bell(user):
    """(type, role, booking_id) of everything in this person's bell, newest first."""
    return [(n.type, n.role, n.booking_id) for n in Notification.objects.filter(user=user)]


def act(user, b, action, **body):
    return client(user).post(f"/api/bookings/{b.pk}/{action}/", body, format="json")


@pytest.fixture
def stranger(django_user_model):
    return django_user_model.objects.create_user(phone="+919800000009", firebase_uid="x", name="X")


# --------------------------------------------------------------------- who hears what


class TestWhoHearsWhat:
    def test_a_new_request_reaches_the_owner_only(self, tractor, owner, renter):
        res = client(renter).post("/api/bookings/", {
            "option_id": tractor.opt_cult.pk, "start_at": at(TUE, 8).isoformat(), "hours": 3,
            "field": {"lat": "25.51", "lng": "81.86", "village": "सोरांव"},
        }, format="json")
        assert res.status_code == 201, res.json()
        assert bell(owner) == [("booking_requested", "owner", res.json()["id"])]
        assert bell(renter) == []
        n = Notification.objects.get(user=owner)
        # The words come from the app's ARB; the server keeps only what they need (snapshot).
        assert n.params == {
            "renter": "Suresh", "owner": "Ramesh", "machine": "ट्रैक्टर", "implement": "कल्टीवेटर",
            "start": "2026-11-24T08:00:00+05:30", "end": "2026-11-24T11:00:00+05:30",
        }

    def test_a_refused_request_leaves_nothing(self, tractor, owner, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 11)  # taken
        res = client(renter).post("/api/bookings/", {
            "option_id": tractor.opt_hour.pk, "start_at": at(TUE, 9).isoformat(), "hours": 2,
            "field": {"lat": "25.51", "lng": "81.86"},
        }, format="json")
        assert res.status_code == 409
        assert Notification.objects.count() == 0

    @pytest.mark.parametrize("action, body, start, to_renter, to_owner", [
        ("accept", {}, S.REQUESTED, "booking_accepted", None),
        ("reject", {"reason": "busy"}, S.REQUESTED, "booking_rejected", None),
        ("cancel", {"reason": "repair"}, S.ACCEPTED, "booking_cancelled_by_owner", None),
    ])
    def test_the_owner_acts_the_renter_hears(self, tractor, owner, renter, action, body, start, to_renter, to_owner):
        b = waiting(tractor, renter)
        Booking.objects.filter(pk=b.pk).update(status=start)
        assert act(owner, b, action, **body).status_code == 200
        assert bell(renter) == [(to_renter, "renter", b.pk)]
        assert bell(owner) == []

    @pytest.mark.parametrize("start, body", [(S.REQUESTED, {}), (S.ACCEPTED, {"reason": "weather"})])
    def test_the_renter_cancels_the_owner_hears(self, tractor, owner, renter, start, body):
        b = waiting(tractor, renter)
        Booking.objects.filter(pk=b.pk).update(status=start)
        assert act(renter, b, "cancel", **body).status_code == 200
        assert bell(owner) == [("booking_cancelled_by_renter", "owner", b.pk)]
        assert bell(renter) == []

    def test_the_owner_completes_the_renter_hears(self, tractor, owner, renter, frozen):
        b = book(tractor, tractor.opt_hour, renter, MON, 8, 10)
        frozen.move_to("2026-11-23 05:00:00")  # 10:30 IST, the work has begun
        assert act(owner, b, "complete").status_code == 200
        assert bell(renter) == [("booking_completed", "renter", b.pk)]
        assert bell(owner) == []

    def test_a_refused_action_leaves_nothing(self, tractor, owner, renter):
        b = waiting(tractor, renter)
        assert act(renter, b, "cancel").status_code == 200
        Notification.objects.all().delete()
        assert act(owner, b, "accept").status_code == 409  # the renter was first
        assert Notification.objects.count() == 0

    def test_time_runs_out_both_hear_with_their_own_words(self, tractor, owner, renter):
        """The approved renter text "मालिक ने समय पर जवाब नहीं दिया…" would read wrong for the owner."""
        b = waiting(tractor, renter, expires_at=at(MON, 7))  # already overdue
        res = client(owner).get("/api/notifications/unread-count/")  # the bell alone catches it up
        assert res.json() == {"count": 1}
        assert bell(owner) == [("booking_expired_owner", "owner", b.pk)]
        assert bell(renter) == [("booking_expired", "renter", b.pk)]
        # Reading again doesn't tell anyone twice.
        client(renter).get("/api/notifications/")
        client(owner).get("/api/bookings/", {"role": "owner"})
        assert Notification.objects.count() == 2

    def test_run_booking_jobs_tells_both(self, tractor, owner, renter, frozen):
        waiting(tractor, renter, expires_at=at(MON, 7))
        done = book(tractor, tractor.opt_hour, renter, MON, 8, 10)
        frozen.move_to(at(MON, 10) + timedelta(hours=12))  # 12 h after the work ended
        call_command("run_booking_jobs")
        assert sorted(t for t, _, _ in bell(owner)) == ["booking_completed", "booking_expired_owner"]
        assert sorted(t for t, _, _ in bell(renter)) == ["booking_completed", "booking_expired"]
        assert ("booking_completed", "owner", done.pk) in bell(owner)

    def test_a_per_day_group_is_one_notification_for_its_first_day(self, tractor, owner, renter):
        days = [waiting(tractor, renter, day=d, start=6, end=18) for d in (WED, TUE)]
        Booking.objects.filter(pk__in=[d.pk for d in days]).update(group_id=uuid.uuid4())
        assert act(owner, days[0], "accept").status_code == 200
        assert bell(renter) == [("booking_accepted", "renter", days[1].pk)]  # TUE = the first day
        n = Notification.objects.get()
        assert (n.params["start"], n.params["end"]) == ("2026-11-24T06:00:00+05:30", "2026-11-25T18:00:00+05:30")

    def test_the_typed_names_of_other_machines(self, tractor, owner, renter):
        tractor.machine_type_other = "मेरी मशीन"
        tractor.save()
        o = tractor.opt_cult
        o.implement_other = "हल"
        o.save()
        b = book(tractor, o, renter, TUE, 8, 11, status=S.REQUESTED, expires_at=LATER)
        act(owner, b, "accept")
        p = Notification.objects.get().params
        assert (p["machine"], p["implement"]) == ("मेरी मशीन", "हल")

    def test_the_machine_alone_has_no_implement(self, tractor, owner, renter):
        b = waiting(tractor, renter)
        act(owner, b, "accept")
        assert Notification.objects.get().params["implement"] is None


# --------------------------------------------------------------------- the endpoints


class TestTheBell:
    def make(self, user, n, **kw):
        return [Notification.objects.create(user=user, type="booking_accepted", role="renter", params={}, **kw)
                for _ in range(n)]

    def test_list_mine_only_newest_first_20_per_page(self, renter, owner, frozen):
        mine = []
        for i in range(23):
            frozen.tick(timedelta(minutes=1))
            mine += self.make(renter, 1)
        self.make(owner, 2)
        res = client(renter).get("/api/notifications/")
        j = res.json()
        assert res.status_code == 200 and j["count"] == 23 and j["next_page"] == 2
        assert [r["id"] for r in j["results"]] == [n.pk for n in reversed(mine)][:20]
        first = j["results"][0]
        assert set(first) == {"id", "type", "role", "booking_id", "params", "read_at", "created_at"}
        assert first["created_at"].endswith("+05:30") and first["read_at"] is None
        assert len(client(renter).get("/api/notifications/", {"page": 2}).json()["results"]) == 3

    def test_unread_count_and_read_by_ids(self, renter, owner):
        a, b, c = self.make(renter, 3)
        (theirs,) = self.make(owner, 1)
        me = client(renter)
        assert me.get("/api/notifications/unread-count/").json() == {"count": 3}
        res = me.post("/api/notifications/read/", {"ids": [a.pk, b.pk, theirs.pk]}, format="json")
        assert res.status_code == 204
        assert me.get("/api/notifications/unread-count/").json() == {"count": 1}
        theirs.refresh_from_db()
        assert theirs.read_at is None  # someone else's id does nothing
        listed = {r["id"]: r["read_at"] for r in me.get("/api/notifications/").json()["results"]}
        assert listed[a.pk] and listed[c.pk] is None

    def test_read_all_keeps_the_first_read_time(self, renter, frozen):
        a, b = self.make(renter, 2)
        me = client(renter)
        me.post("/api/notifications/read/", {"ids": [a.pk]}, format="json")
        first = Notification.objects.get(pk=a.pk).read_at
        frozen.tick(timedelta(hours=1))
        assert me.post("/api/notifications/read/", {"all": True}, format="json").status_code == 204
        assert me.get("/api/notifications/unread-count/").json() == {"count": 0}
        assert Notification.objects.get(pk=a.pk).read_at == first

    def test_read_needs_ids_or_all(self, renter):
        res = client(renter).post("/api/notifications/read/", {}, format="json")
        assert res.status_code == 400
        assert res.json()["error"]["fields"] == {"ids": "required"}

    def test_logged_out(self, api):
        assert api.get("/api/notifications/").status_code == 401
        assert api.get("/api/notifications/unread-count/").status_code == 401
        assert api.post("/api/notifications/read/", {"all": True}, format="json").status_code == 401

    def test_a_stranger_sees_nothing(self, tractor, owner, renter, stranger):
        act(owner, waiting(tractor, renter), "accept")
        assert client(stranger).get("/api/notifications/").json()["count"] == 0
