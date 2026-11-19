from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def home(request):
    return JsonResponse({"status": "API is running"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("", home),
    path("api/", include("accounts.urls")),
    path("api/", include("catalog.urls")),
    path("api/", include("listings.urls")),
]
