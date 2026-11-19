"""
One error format for the whole API:

    {"error": {"code": "invalid_token", "fields": {...}}}

The app turns `code` into a Hindi message. The server never sends display text.
"""

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.views import exception_handler
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError


class ApiError(exceptions.APIException):
    """Raise this with a code from docs/06-api.md, e.g. ApiError("phone_not_supported")."""

    status_code = status.HTTP_400_BAD_REQUEST

    def __init__(self, code: str, http_status: int | None = None, fields: dict | None = None):
        self.error_code = code
        self.fields = fields or {}
        if http_status:
            self.status_code = http_status
        super().__init__(detail=code, code=code)


def _code_for(exc) -> str:
    if isinstance(exc, ApiError):
        return exc.error_code
    if isinstance(exc, (InvalidToken, TokenError, exceptions.AuthenticationFailed, exceptions.NotAuthenticated)):
        return "invalid_token"
    if isinstance(exc, exceptions.ValidationError):
        return "validation_error"
    if isinstance(exc, exceptions.Throttled):
        return "rate_limited"
    # Django's own Http404 (get_object_or_404) is turned into a 404 by DRF, but keeps its own class.
    if isinstance(exc, (exceptions.NotFound, Http404)):
        return "not_found"
    if isinstance(exc, (exceptions.PermissionDenied, DjangoPermissionDenied)):
        return "permission_denied"
    if isinstance(exc, exceptions.MethodNotAllowed):
        return "method_not_allowed"
    return "server_error"


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None  # unexpected crash: Django's normal 500 (logged)

    fields = {}
    if isinstance(exc, ApiError):
        fields = exc.fields
    elif isinstance(exc, exceptions.ValidationError) and isinstance(response.data, dict):
        fields = response.data

    # TokenError from SimpleJWT views arrives as 401; keep the status DRF chose.
    response.data = {"error": {"code": _code_for(exc), "fields": fields}}
    return response
