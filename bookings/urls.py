from django.urls import path

from . import views

urlpatterns = [
    path("listings/<int:pk>/calendar/", views.CalendarView.as_view(), name="listing-calendar"),
    path("listings/<int:pk>/start-times/", views.StartTimesView.as_view(), name="listing-start-times"),
    path("listings/<int:pk>/days/<str:day>/", views.DayView.as_view(), name="listing-day"),
]
