"""
Talking to Firebase.

To check a Firebase ID token we only need the project ID: Firebase signs tokens
with public Google keys, and firebase-admin downloads those public keys itself.
So no secret service-account file is needed for login. (Push notifications and
deleting Firebase users later will need one; that comes in a later phase.)
"""

import firebase_admin
from django.conf import settings
from firebase_admin import auth as firebase_auth
from firebase_admin import credentials
from google.auth.credentials import AnonymousCredentials

_app = None


class _NoSecretCredential(credentials.Base):
    """Tells firebase-admin 'there is no secret key here'. Checking ID tokens only uses
    Google's public keys, so this is enough. Without it, firebase-admin goes looking
    for a key file on the computer and fails."""

    def get_credential(self):
        return AnonymousCredentials()


def _get_app():
    global _app
    if _app is None:
        try:
            _app = firebase_admin.get_app("agriverse")
        except ValueError:
            _app = firebase_admin.initialize_app(
                credential=_NoSecretCredential(),
                options={"projectId": settings.FIREBASE_PROJECT_ID},
                name="agriverse",
            )
    return _app


class InvalidFirebaseToken(Exception):
    """The token is wrong, expired or fake: the user must verify the phone again."""


class FirebaseUnavailable(Exception):
    """We couldn't reach Google to check the token. Not the user's fault: try again later."""


def verify_id_token(token: str) -> dict:
    """Return the decoded token (has 'uid' and 'phone_number'), or raise one of the errors above."""
    try:
        return firebase_auth.verify_id_token(token, app=_get_app(), clock_skew_seconds=10)
    except firebase_auth.CertificateFetchError as exc:
        raise FirebaseUnavailable(str(exc)) from exc
    except (ValueError, firebase_auth.InvalidIdTokenError, firebase_auth.ExpiredIdTokenError,
            firebase_auth.RevokedIdTokenError, firebase_auth.UserDisabledError) as exc:
        raise InvalidFirebaseToken(str(exc)) from exc
