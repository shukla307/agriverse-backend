"""Owner answers, cancel, complete, the owner's list, run_booking_jobs (Phase 5 slice 4; 06 §8).

Clock: Monday 2026-11-23 07:30 IST (conftest). Ramesh's tractor: every day 6–18, gap 1 hour. Suresh rents.
"""

import threading
import uuid
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from bookings.actions import RULES
from bookings.availability import at
from bookings.models import Booking, BookingEvent

from .conftest import MON, TUE, WED, book, starts

S = Booking.Status
LATER = at(TUE, 6)  # far enough for a waiting request not to be overdue


def url(b, action):
    return f"/api/bookings/{b.pk}/{action}/"


def code(res):
    return res.json()["error"]["code"]


def waiting(tractor, renter, day=TUE, start=8, end=11, **kw):
    return book(tractor, tractor.opt_hour, renter, day, start, end, status=S.REQUESTED,
                expires_at=kw.pop("expires_at", LATER), **kw)


def client(user):
    """A client of its own: the conftest's as_owner / as_renter share one, so one test can't use both."""
    c = APIClient()
    c.force_authenticate(user)
    return c


@pytest.fixture
def stranger(django_user_model):
    return django_user_model.objects.create_user(phone="+919800000009", firebase_uid="x", name="X")


@pytest.fixture
def as_stranger(stranger):
    return client(stranger)


# --------------------------------------------------------------------- the table

ALL = [s.value for s in S]
CASES = [
    (action, who, start, RULES.get((action, who)))
    for action in ("accept", "reject", "cancel", "complete")
    for who in ("owner", "renter")
    for start in ALL
]


@pytest.mark.parametrize("action,who,start,rule", CASES, ids=[f"{a}-{w}-{s}" for a, w, s, _ in CASES])
def test_every_transition(api, owner, renter, tractor, frozen, action, who, start, rule):
    """06 §8 row by row: every (action, who, from-status). Allowed → the new status + one history row;
    not allowed → 403 (wrong person) or 409 (wrong status), and nothing changes."""
    # MON 6–9 has already started at 07:30 (needed for "complete"); its expiry is later today.
    b = book(tractor, tractor.opt_hour, renter, MON, 6, 9, status=start, expires_at=at(MON, 10))
    user = owner if who == "owner" else renter
    api.force_authenticate(user)
    chip = "busy" if who == "owner" else "not_needed"  # each side has its own chips
    body = {"reason": chip} if action in ("reject", "cancel") else {}
    res = api.post(url(b, action), body, format="json")
    b.refresh_from_db()
    if rule is None:
        assert res.status_code == 403, res.json()
        assert b.status == start
    elif start in rule.allowed_from:
        assert res.status_code == 200, res.json()
        assert b.status == rule.to and res.json()["status"] == rule.to
        e = b.events.get()
        assert (e.from_status, e.to_status, e.actor_id) == (start, rule.to, user.pk)
    elif start == "expired" and action == "accept":
        assert res.status_code == 409 and code(res) == "expired"
        assert b.status == start and not b.events.exists()
    else:
        assert res.status_code == 409, res.json()
        assert code(res) == "invalid_status" and res.json()["error"]["fields"]["status"] == start
        assert b.status == start and not b.events.exists()


def test_the_table_is_what_the_owner_approved():
    # The last column: from which statuses a reason chip is required (slice 5: the renter only for a
    # confirmed booking; a waiting request is cancelled with one tap).
    assert {k: (r.allowed_from, r.to, r.reason_from) for k, r in RULES.items()} == {
        ("accept", "owner"): (("requested",), "accepted", ()),
        ("reject", "owner"): (("requested",), "rejected", ("requested",)),
        ("cancel", "renter"): (("requested", "accepted"), "cancelled_by_renter", ("accepted",)),
        ("cancel", "owner"): (("accepted",), "cancelled_by_owner", ("accepted",)),
        ("complete", "owner"): (("accepted",), "completed", ()),
    }


# --------------------------------------------------------------------- accept / reject


class TestAccept:
    def test_both_sides_get_the_contact(self, as_owner, tractor, renter):
        b = waiting(tractor, renter)
        b.field_landmark = "पुल के पास"
        b.save()
        before = as_owner.get(f"/api/bookings/{b.pk}/").json()
        assert "contact" not in before and "पुल के पास" not in str(before)
        j = as_owner.post(url(b, "accept"), format="json").json()
        assert j["status"] == "accepted"
        assert j["contact"]["renter_phone"] == "+919812345678"
        assert j["contact"]["field"]["landmark"] == "पुल के पास"
        b.refresh_from_db()
        assert b.accepted_at is not None and b.responded_at is not None

    def test_too_late_is_expired_and_the_time_is_free_again(self, as_owner, tractor, renter, frozen):
        b = waiting(tractor, renter, expires_at=at(MON, 9))
        frozen.move_to("2026-11-23 03:31:00")  # 09:01 IST: the answer time has passed
        res = as_owner.post(url(b, "accept"), format="json")
        assert res.status_code == 409 and code(res) == "expired"
        b.refresh_from_db()
        assert b.status == "expired"
        assert list(b.events.values_list("to_status", flat=True)) == ["expired"]
        assert 8 in starts(client(renter), tractor, TUE).json()["start_hours"]

    def test_reject_needs_a_reason(self, as_owner, tractor, renter):
        b = waiting(tractor, renter)
        res = as_owner.post(url(b, "reject"), {}, format="json")
        assert res.status_code == 400 and res.json()["error"]["fields"] == {"reason": "required"}
        res = as_owner.post(url(b, "reject"), {"reason": "other"}, format="json")
        assert res.status_code == 400 and "reason_other" in res.json()["error"]["fields"]
        res = as_owner.post(url(b, "reject"), {"reason": "other", "reason_other": "  ट्रैक्टर बाहर है "}, format="json")
        assert res.status_code == 200
        b.refresh_from_db()
        assert (b.status, b.reason, b.reason_other) == ("rejected", "other", "ट्रैक्टर बाहर है")
        assert b.events.get().reason == "other"

    def test_a_chip_reason_drops_stray_text(self, as_owner, tractor, renter):
        b = waiting(tractor, renter)
        as_owner.post(url(b, "reject"), {"reason": "repair", "reason_other": "x"}, format="json")
        b.refresh_from_db()
        assert (b.reason, b.reason_other) == ("repair", "")

    def test_rejected_time_is_free_again(self, as_owner, tractor, renter):
        b = waiting(tractor, renter)
        as_owner.post(url(b, "reject"), {"reason": "busy"}, format="json")
        assert 8 in starts(client(renter), tractor, TUE).json()["start_hours"]

    def test_owner_cancel_needs_a_reason(self, as_owner, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        assert code(as_owner.post(url(b, "cancel"), {}, format="json")) == "validation_error"
        assert as_owner.post(url(b, "cancel"), {"reason": "repair"}, format="json").json()["status"] == \
            "cancelled_by_owner"

    def test_renter_cancel_without_a_reason(self, as_renter, tractor, renter):
        b = waiting(tractor, renter)
        res = as_renter.post(url(b, "cancel"), {}, format="json")
        assert res.status_code == 200 and res.json()["status"] == "cancelled_by_renter"


class TestRenterCancelsConfirmed:
    """Slice 5 (decision §21): after acceptance the renter talks first, then gives a reason, like the owner."""

    def test_needs_a_reason(self, as_renter, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = as_renter.post(url(b, "cancel"), {}, format="json")
        assert res.status_code == 400 and res.json()["error"]["fields"] == {"reason": "required"}
        b.refresh_from_db()
        assert b.status == "accepted" and not b.events.exists()

    @pytest.mark.parametrize("chip", ["not_needed", "found_other", "weather"])
    def test_with_a_renter_chip(self, as_renter, tractor, renter, chip):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        j = as_renter.post(url(b, "cancel"), {"reason": chip}, format="json").json()
        assert (j["status"], j["reason"]) == ("cancelled_by_renter", chip)
        assert "contact" not in j  # the phones go away again
        assert b.events.get().reason == chip

    def test_other_needs_words(self, as_renter, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = as_renter.post(url(b, "cancel"), {"reason": "other"}, format="json")
        assert res.status_code == 400 and "reason_other" in res.json()["error"]["fields"]
        j = as_renter.post(url(b, "cancel"), {"reason": "other", "reason_other": "बेटे की शादी"}, format="json").json()
        assert (j["reason"], j["reason_other"]) == ("other", "बेटे की शादी")

    @pytest.mark.parametrize("who,chip", [("renter", "repair"), ("renter", "too_far"), ("renter", "busy"),
                                          ("owner", "not_needed"), ("owner", "weather")])
    def test_the_other_sides_chip_is_refused(self, owner, renter, tractor, who, chip):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = client(renter if who == "renter" else owner).post(url(b, "cancel"), {"reason": chip}, format="json")
        assert res.status_code == 400 and res.json()["error"]["fields"] == {"reason": "invalid"}
        b.refresh_from_db()
        assert b.status == "accepted"

    def test_accepted_meanwhile_says_it_changed(self, as_renter, tractor, renter, monkeypatch):
        """S12's one-tap cancel while the owner accepts: the renter's check saw "requested", the lock sees
        "accepted" → 409 invalid_status (the app reloads and asks properly), never a cancel without a reason."""
        b = waiting(tractor, renter)
        from bookings import actions

        real = actions.expire_overdue

        def owner_accepts_now(qs, now):  # runs inside the lock, after the first status read
            Booking.objects.filter(pk=b.pk).update(status=S.ACCEPTED)
            return real(qs, now)

        monkeypatch.setattr(actions, "expire_overdue", owner_accepts_now)
        res = as_renter.post(url(b, "cancel"), {}, format="json")
        assert res.status_code == 409 and code(res) == "invalid_status"
        assert res.json()["error"]["fields"]["status"] == "accepted"
        b.refresh_from_db()
        assert b.status == "accepted" and not b.events.exists()


class TestComplete:
    def test_not_before_the_work_starts(self, as_owner, tractor, renter):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        res = as_owner.post(url(b, "complete"), format="json")
        assert res.status_code == 409 and code(res) == "not_started"

    def test_after_it_started(self, as_owner, tractor, renter, frozen):
        b = book(tractor, tractor.opt_hour, renter, TUE, 8, 11)
        frozen.move_to("2026-11-24 02:30:00")  # Tue 08:00 IST
        assert as_owner.post(url(b, "complete"), format="json").json()["status"] == "completed"


class TestWhoMay:
    def test_stranger_gets_404(self, as_stranger, tractor, renter):
        b = waiting(tractor, renter)
        assert as_stranger.post(url(b, "accept"), format="json").status_code == 404
        assert as_stranger.post(url(b, "cancel"), format="json").status_code == 404

    def test_unknown_action_is_404(self, as_owner, tractor, renter):
        assert as_owner.post(url(waiting(tractor, renter), "approve"), format="json").status_code == 404


class TestPerDayGroup:
    def test_one_answer_for_every_day(self, as_owner, tractor, renter):
        gid = uuid.uuid4()
        days = [book(tractor, tractor.opt_day, renter, d, 6, 18, status=S.REQUESTED, expires_at=LATER)
                for d in (TUE, WED)]
        Booking.objects.filter(pk__in=[d.pk for d in days]).update(group_id=gid)
        j = as_owner.post(url(days[1], "accept"), format="json").json()
        assert j["status"] == "accepted" and len(j["days"]) == 2
        assert set(Booking.objects.filter(group_id=gid).values_list("status", flat=True)) == {"accepted"}
        assert BookingEvent.objects.filter(booking__group_id=gid, to_status="accepted").count() == 2


# --------------------------------------------------------------------- the owner's list


class TestOwnerList:
    URL = "/api/bookings/"

    def test_requests_to_answer_on_top_soonest_answer_time_first(self, as_owner, tractor, renter, frozen):
        tomorrow = waiting(tractor, renter, TUE, 8, 10, expires_at=at(MON, 9) + timedelta(minutes=30))
        frozen.tick(timedelta(minutes=1))
        accepted = book(tractor, tractor.opt_hour, renter, TUE, 13, 15)
        frozen.tick(timedelta(minutes=1))
        later = waiting(tractor, renter, WED, 8, 10, expires_at=at(MON, 13))  # newer, but 6 h to answer
        frozen.tick(timedelta(minutes=1))
        newest_done = book(tractor, tractor.opt_hour, renter, WED, 14, 16, status=S.REJECTED)
        j = as_owner.get(self.URL, {"role": "owner"}).json()
        assert j["count"] == 4
        assert [r["id"] for r in j["results"]] == [tomorrow.pk, later.pk, newest_done.pk, accepted.pk]
        first = j["results"][1]
        assert first["renter"] == {"name": "Suresh", "village": "सोरांव"}
        assert first["field"]["distance_km"] == 8.8  # झूंसी → the field near सोरांव, rounded to 0.1 km

    def test_count_of_requests_to_answer(self, as_owner, tractor, renter):
        waiting(tractor, renter)
        book(tractor, tractor.opt_hour, renter, TUE, 13, 15)
        j = as_owner.get(self.URL, {"role": "owner", "status": "requested"}).json()
        assert j["count"] == 1

    def test_overdue_requests_show_as_expired(self, as_owner, tractor, renter, frozen):
        b = waiting(tractor, renter, expires_at=at(MON, 9))
        frozen.move_to("2026-11-23 04:00:00")
        assert as_owner.get(self.URL, {"role": "owner", "status": "requested"}).json()["count"] == 0
        b.refresh_from_db()
        assert b.status == "expired"

    def test_only_my_machines(self, as_stranger, tractor, renter):
        waiting(tractor, renter)
        assert as_stranger.get(self.URL, {"role": "owner"}).json()["count"] == 0
        assert client(renter).get(self.URL, {"role": "owner"}).json()["count"] == 0
        assert client(renter).get(self.URL, {"role": "renter"}).json()["count"] == 1

    def test_a_group_shows_once(self, as_owner, tractor, renter):
        gid = uuid.uuid4()
        for d in (TUE, WED):
            book(tractor, tractor.opt_day, renter, d, 6, 18, status=S.REQUESTED, expires_at=LATER)
        Booking.objects.update(group_id=gid)
        j = as_owner.get(self.URL, {"role": "owner"}).json()
        assert j["count"] == 1 and len(j["results"][0]["days"]) == 2

    def test_queries_dont_grow_with_the_list(self, as_owner, tractor, renter):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                assert as_owner.get(self.URL, {"role": "owner"}).status_code == 200
            return len(ctx.captured_queries)

        waiting(tractor, renter, TUE, 6, 7)
        one = queries()
        for start in (9, 12, 15):
            waiting(tractor, renter, TUE, start, start + 1)
        assert queries() == one

    def test_bad_params(self, as_owner):
        assert as_owner.get(self.URL).status_code == 400
        assert as_owner.get(self.URL, {"role": "owner", "status": "waiting"}).status_code == 400

    def test_reading_is_not_throttled_like_sending(self, as_owner, tractor):
        for _ in range(35):
            assert as_owner.get(self.URL, {"role": "owner"}).status_code == 200


class TestRenterList:
    """Slice 5 "मेरी बुकिंग": what is still on first (soonest work on top), then the rest newest first."""

    URL = "/api/bookings/"

    def test_on_going_first_soonest_work_on_top(self, as_renter, tractor, renter, frozen):
        wed_confirmed = book(tractor, tractor.opt_hour, renter, WED, 8, 10)
        frozen.tick(timedelta(minutes=1))
        old_rejected = book(tractor, tractor.opt_hour, renter, TUE, 6, 7, status=S.REJECTED)
        frozen.tick(timedelta(minutes=1))
        tue_waiting = waiting(tractor, renter, TUE, 13, 15)
        frozen.tick(timedelta(minutes=1))
        tue_confirmed = book(tractor, tractor.opt_hour, renter, TUE, 8, 10)  # newest, but not the soonest
        frozen.tick(timedelta(minutes=1))
        new_cancelled = book(tractor, tractor.opt_hour, renter, WED, 14, 16, status=S.CANCELLED_BY_RENTER)
        j = as_renter.get(self.URL, {"role": "renter"}).json()
        assert [r["id"] for r in j["results"]] == [
            tue_confirmed.pk, tue_waiting.pk, wed_confirmed.pk,  # on going: by start time
            new_cancelled.pk, old_rejected.pk,  # finished: newest first
        ]
        assert j["results"][0]["owner"] == {"name": "Ramesh"}

    def test_home_card_asks_only_for_what_is_on(self, as_renter, tractor, renter):
        book(tractor, tractor.opt_hour, renter, TUE, 8, 10)
        waiting(tractor, renter, TUE, 13, 15)
        book(tractor, tractor.opt_hour, renter, WED, 8, 10, status=S.COMPLETED)
        j = as_renter.get(self.URL, {"role": "renter", "status": "requested,accepted"}).json()
        assert j["count"] == 2

    def test_contact_only_on_the_confirmed_one(self, as_renter, tractor, renter):
        confirmed = book(tractor, tractor.opt_hour, renter, TUE, 8, 10)
        waiting(tractor, renter, TUE, 13, 15)
        rows = {r["id"]: r for r in as_renter.get(self.URL, {"role": "renter"}).json()["results"]}
        assert [i for i, r in rows.items() if "contact" in r] == [confirmed.pk]
        assert rows[confirmed.pk]["contact"]["owner_phone"].startswith("+91")

    def test_only_mine(self, as_owner, tractor, renter):
        waiting(tractor, renter)
        assert as_owner.get(self.URL, {"role": "renter"}).json()["count"] == 0

    def test_queries_dont_grow_with_the_list(self, as_renter, tractor, renter):
        def queries():
            with CaptureQueriesContext(connection) as ctx:
                assert as_renter.get(self.URL, {"role": "renter"}).status_code == 200
            return len(ctx.captured_queries)

        waiting(tractor, renter, TUE, 6, 7)
        one = queries()
        for start in (9, 12):
            waiting(tractor, renter, TUE, start, start + 1)
        book(tractor, tractor.opt_hour, renter, TUE, 15, 16)
        assert queries() == one


# --------------------------------------------------------------------- run_booking_jobs


class TestJobs:
    def run(self):
        call_command("run_booking_jobs")

    def test_expires_unanswered_requests(self, tractor, renter, frozen, as_renter):
        b = waiting(tractor, renter, expires_at=at(MON, 9))
        frozen.move_to("2026-11-23 03:31:00")
        self.run()
        b.refresh_from_db()
        assert b.status == "expired"
        e = b.events.get()
        assert (e.from_status, e.to_status, e.actor_id) == ("requested", "expired", None)
        assert 8 in starts(as_renter, tractor, TUE).json()["start_hours"]

    def test_completes_12_hours_after_the_work(self, tractor, renter, frozen):
        b = book(tractor, tractor.opt_hour, renter, MON, 8, 11)
        frozen.move_to("2026-11-23 17:29:00")  # 22:59 IST: 11 h 59 min after
        self.run()
        b.refresh_from_db()
        assert b.status == "accepted"
        frozen.move_to("2026-11-23 17:30:00")  # 23:00 IST
        self.run()
        b.refresh_from_db()
        assert b.status == "completed" and b.completed_at is not None
        self.run()  # running again changes nothing
        assert b.events.count() == 1

    def test_a_group_waits_for_its_last_day(self, tractor, renter, frozen):
        gid = uuid.uuid4()
        days = [book(tractor, tractor.opt_day, renter, d, 6, 18) for d in (MON, TUE)]
        Booking.objects.update(group_id=gid)
        frozen.move_to("2026-11-24 01:00:00")  # Tue 06:30: Monday ended 12.5 h ago, Tuesday not yet
        self.run()
        assert set(Booking.objects.values_list("status", flat=True)) == {"accepted"}
        frozen.move_to("2026-11-24 19:00:00")  # Wed 00:30 IST = Tue 18:00 + 6.5 h … not yet
        self.run()
        assert set(Booking.objects.values_list("status", flat=True)) == {"accepted"}
        frozen.move_to("2026-11-25 01:00:00")  # Wed 06:30 IST: 12.5 h after Tuesday's work
        self.run()
        assert [Booking.objects.get(pk=d.pk).status for d in days] == ["completed", "completed"]

    def test_leaves_the_rest_alone(self, tractor, renter, frozen):
        keep = [waiting(tractor, renter), book(tractor, tractor.opt_hour, renter, WED, 8, 11),
                book(tractor, tractor.opt_hour, renter, TUE, 13, 15, status=S.REJECTED)]
        self.run()
        assert [Booking.objects.get(pk=b.pk).status for b in keep] == ["requested", "accepted", "rejected"]


# --------------------------------------------------------------------- at the same moment


@pytest.mark.django_db(transaction=True, serialized_rollback=True)
class TestRejectAndCancelAtOnce:
    """The owner taps नहीं while the renter cancels: exactly one wins, the other gets 409 (the lock).
    (Accept + cancel can both succeed: a renter may cancel an accepted booking.)"""

    def test_exactly_one_wins(self, owner, renter, tractor):
        b = waiting(tractor, renter)
        barrier = threading.Barrier(2)
        results = {}

        def go(user, action):
            c = client(user)
            try:
                barrier.wait()
                chip = "busy" if action == "reject" else "not_needed"  # each side's own chip
                results[action] = c.post(url(b, action), {"reason": chip}, format="json").status_code
            finally:
                connection.close()

        threads = [threading.Thread(target=go, args=(owner, "reject")),
                   threading.Thread(target=go, args=(renter, "cancel"))]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sorted(results.values()) == [200, 409]
        b.refresh_from_db()
        winner = next(a for a, c in results.items() if c == 200)
        assert b.status == {"reject": "rejected", "cancel": "cancelled_by_renter"}[winner]
        assert b.events.count() == 1
