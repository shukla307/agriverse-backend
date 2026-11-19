"""One page format for every list (docs/06-api.md §1): 20 per page, ?page=N.

    {"count": 57, "next_page": 2, "results": [...]}     next_page is null on the last page
"""

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class Pages(PageNumberPagination):
    page_size = 20

    def get_paginated_response(self, data):
        nxt = self.page.next_page_number() if self.page.has_next() else None
        return Response({"count": self.page.paginator.count, "next_page": nxt, "results": data})
