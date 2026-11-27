"""
Pushes through Firebase Cloud Messaging (Phase 5 slice 7; 07 §4).

    notify.booking_changed() saves Notification rows inside the booking's transaction and calls after_commit().
    after_commit() waits until that transaction is really saved (transaction.on_commit), then sends.

Rules:
- The Notification table is the truth; a push only announces a row that already exists. So a push that fails
  (no internet, Firebase down, no key on this computer) is logged and forgotten: it NEVER fails the booking.
- A phone's address is its Firebase installation ID (FID), from the app's FirebaseMessaging.register(); the
  model/API keep the name `token`.
- Firebase says a token is dead (app uninstalled / data cleared / another project) → that DeviceToken is deleted.
- Only `booking_requested` is sent as a HIGH-priority message: the owner has 2 hours to answer (07 §4).
- The key: FIREBASE_CREDENTIALS_PATH in .env = a service-account JSON file OUTSIDE git, allowed to send
  pushes only ("Firebase Cloud Messaging API Admin"). No path → pushes are off (tests, a fresh laptop).
"""

import logging
from pathlib import Path

import firebase_admin
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from firebase_admin import credentials, messaging

from . import push_text
from .models import DeviceToken, Notification

log = logging.getLogger(__name__)

CHANNEL_ID = "bookings"  # the app creates this Android channel ("बुकिंग की सूचनाएँ", high importance)
HIGH_PRIORITY = {Notification.Type.BOOKING_REQUESTED}
DEAD_TOKEN_ERRORS = (messaging.UnregisteredError, messaging.SenderIdMismatchError)

_app = None


def _get_app():
    """The firebase-admin app that may send (separate from accounts/firebase.py, which needs no key)."""
    global _app
    if _app is None:
        path = Path(settings.FIREBASE_CREDENTIALS_PATH).expanduser() if settings.FIREBASE_CREDENTIALS_PATH else None
        if path is None or not path.is_file():
            return None
        try:
            _app = firebase_admin.get_app("agriverse-push")
        except ValueError:
            _app = firebase_admin.initialize_app(
                credential=credentials.Certificate(str(path)),
                options={"projectId": settings.FIREBASE_PROJECT_ID, "httpTimeout": 5},
                name="agriverse-push",
            )
    return _app


def _send_each(messages: list[messaging.Message]) -> list[Exception | None]:
    """One answer per message: None = delivered to Firebase, else the error. Tests replace this."""
    app = _get_app()
    if app is None:
        log.info("Push is off: FIREBASE_CREDENTIALS_PATH is not set or the file is missing.")
        return [None] * len(messages)
    batch = messaging.send_each(messages, app=app)
    return [None if r.success else r.exception for r in batch.responses]


def message(n: Notification, token: str, now) -> messaging.Message:
    high = n.type in HIGH_PRIORITY
    return messaging.Message(
        fid=token,  # the phone's Firebase installation ID (FCM's direct-send target; `token=` is deprecated)
        # The app reads `data` when the push is tapped (or arrives while it's open): which screen to open.
        data={"type": n.type, "role": n.role, "booking_id": str(n.booking_id or ""), "notification_id": str(n.pk)},
        notification=messaging.Notification(title=push_text.TITLE, body=push_text.text(n.type, n.params, now)),
        android=messaging.AndroidConfig(
            priority="high" if high else "normal",
            # A later push about the same booking replaces the earlier one in the phone's tray.
            notification=messaging.AndroidNotification(
                channel_id=CHANNEL_ID, tag=f"booking-{n.booking_id}" if n.booking_id else None,
                default_sound=True,
            ),
        ),
    )


def send(notifications: list[Notification]) -> int:
    """Send these (already saved) notifications to their people's phones. Returns how many were accepted."""
    if not notifications:
        return 0
    now = timezone.now()
    tokens = DeviceToken.objects.filter(user_id__in={n.user_id for n in notifications})
    by_user: dict[int, list[DeviceToken]] = {}
    for t in tokens:
        by_user.setdefault(t.user_id, []).append(t)
    pairs = [(n, t) for n in notifications for t in by_user.get(n.user_id, [])]
    if not pairs:
        return 0
    try:
        results = _send_each([message(n, t.token, now) for n, t in pairs])
    except Exception:  # noqa: BLE001 — a push must never break what triggered it
        log.exception("Push failed for %d message(s)", len(pairs))
        return 0
    dead = {t.pk for (_, t), err in zip(pairs, results) if isinstance(err, DEAD_TOKEN_ERRORS)}
    if dead:
        DeviceToken.objects.filter(pk__in=dead).delete()
    for (n, _), err in zip(pairs, results):
        if err is not None and not isinstance(err, DEAD_TOKEN_ERRORS):
            log.warning("Push for notification #%s not sent: %s", n.pk, type(err).__name__)
    return sum(err is None for err in results)


def after_commit(notifications: list[Notification]) -> None:
    """Send once the surrounding transaction is saved; nothing is sent if it rolls back."""
    if notifications:
        items = list(notifications)
        transaction.on_commit(lambda: _safe_send(items))


def _safe_send(items):
    try:
        send(items)
    except Exception:  # noqa: BLE001
        log.exception("Push failed")
