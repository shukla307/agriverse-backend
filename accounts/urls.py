from django.urls import path

from .views import AddressDetailView, AddressListView, AppConfigView, FirebaseLoginView, LogoutView, MeView, RefreshView

urlpatterns = [
    path("auth/firebase/", FirebaseLoginView.as_view(), name="auth-firebase"),
    path("auth/token/refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout/", LogoutView.as_view(), name="auth-logout"),
    path("me/", MeView.as_view(), name="me"),
    path("me/addresses/", AddressListView.as_view(), name="me-addresses"),
    path("me/addresses/<int:pk>/", AddressDetailView.as_view(), name="me-address"),
    path("app-config/", AppConfigView.as_view(), name="app-config"),
]
