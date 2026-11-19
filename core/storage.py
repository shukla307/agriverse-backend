"""Firebase Storage links: the one place that knows what they look like."""

from urllib.parse import unquote, urlsplit

from django.conf import settings


def bucket_path(url: str) -> str | None:
    """
    The file's path inside OUR bucket, or None for any other link.
    https://firebasestorage.googleapis.com/v0/b/<bucket>/o/listings%2Fuid%2Fx%2Fa_full.jpg?alt=media&token=…
      → "listings/uid/x/a_full.jpg"
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme != "https" or parts.netloc != "firebasestorage.googleapis.com":
        return None
    prefix = f"/v0/b/{settings.FIREBASE_STORAGE_BUCKET}/o/"
    if not parts.path.startswith(prefix):
        return None
    path = unquote(parts.path[len(prefix):])
    if not path or ".." in path.split("/") or "//" in path:
        return None
    return path
