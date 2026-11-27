from django.urls import path

from . import views

urlpatterns = [
    path("notifications/", views.NotificationsView.as_view(), name="notifications"),
    path("notifications/unread-count/", views.UnreadCountView.as_view(), name="notifications-unread-count"),
    path("notifications/read/", views.ReadView.as_view(), name="notifications-read"),
]
