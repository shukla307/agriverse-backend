from django.urls import path

from . import views

urlpatterns = [
    path("listings/<int:pk>/calendar/", views.CalendarView.as_view(), name="listing-calendar"),
    path("listings/<int:pk>/start-times/", views.StartTimesView.as_view(), name="listing-start-times"),
    path("listings/<int:pk>/days/<str:day>/", views.DayView.as_view(), name="listing-day"),
    path("bookings/quote/", views.QuoteView.as_view(), name="booking-quote"),
    path("bookings/", views.BookingsView.as_view(), name="bookings"),
    path("bookings/<int:pk>/", views.BookingDetailView.as_view(), name="booking-detail"),
    path("bookings/<int:pk>/<str:action>/", views.BookingActionView.as_view(), name="booking-action"),
]
