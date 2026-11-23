import hashlib
import json

from django.db.models import Prefetch
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import JobCategory, MachineType


def catalogue_data() -> dict:
    """The full list, exactly as the app stores it (docs/06-api.md §4). Inactive rows are left out."""
    jobs = JobCategory.objects.filter(is_active=True)
    active_jobs = Prefetch("jobs", queryset=jobs.only("code", "sort_order"))
    machines = MachineType.objects.filter(is_active=True).prefetch_related(active_jobs)
    return {
        "jobs": [
            {"code": j.code, "name_hi": j.name_hi, "icon": j.icon, "sort_order": j.sort_order}
            for j in jobs
        ],
        "machine_types": [
            {
                "id": m.id,
                "code": m.code,
                "name_hi": m.name_hi,
                "kind": m.kind,
                "jobs": [j.code for j in m.jobs.all()],
                "search_aliases": m.search_aliases,
                "icon": m.icon,
                "default_hours_per_acre": (
                    str(m.default_hours_per_acre) if m.default_hours_per_acre is not None else None
                ),
                # What the wizard asks for this machine and which badges S10 shows (owner, 2026-11-23).
                "fuel": m.fuel,
                "operator": m.operator,
                "asks_transport": m.asks_transport,
                "attachable": m.attachable,
                "takes_attachments": m.takes_attachments,
                "has_brand": m.has_brand,
                "acre_price": m.acre_price,
            }
            for m in machines
        ],
    }


class MachineTypesView(APIView):
    """
    GET /api/machine-types/
    The app keeps this list on the phone. It sends back the version it has
    (If-None-Match); if nothing changed we answer 304 with no body, which costs
    almost nothing on a slow connection.
    The version is a fingerprint of the content itself, so any admin edit changes it
    automatically: nobody has to remember to "bump the version".
    """

    def get(self, request):
        data = catalogue_data()
        canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        version = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        etag = f'"{version}"'
        headers = {"ETag": etag, "Cache-Control": "private, no-cache"}

        sent = [t.strip() for t in request.headers.get("If-None-Match", "").split(",")]
        if etag in sent or f"W/{etag}" in sent:
            return Response(status=status.HTTP_304_NOT_MODIFIED, headers=headers)

        return Response({"version": version, **data}, headers=headers)
