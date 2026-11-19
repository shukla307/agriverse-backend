from django.urls import path

from . import views

urlpatterns = [
    path("listings/", views.ListingCreateView.as_view(), name="listings"),
    path("listings/mine/", views.ListingMineView.as_view(), name="listings-mine"),
    path("listings/<int:pk>/", views.ListingDetailView.as_view(), name="listing"),
    path("listings/<int:pk>/options/", views.OptionListView.as_view(), name="listing-options"),
    path("listings/<int:pk>/options/<int:opt_id>/", views.OptionDetailView.as_view(), name="listing-option"),
    path("listings/<int:pk>/photos/", views.PhotoListView.as_view(), name="listing-photos"),
    path("listings/<int:pk>/photos/order/", views.PhotoOrderView.as_view(), name="listing-photos-order"),
    path("listings/<int:pk>/photos/<int:photo_id>/", views.PhotoDetailView.as_view(), name="listing-photo"),
    path("listings/<int:pk>/weekly-hours/", views.WeeklyHoursView.as_view(), name="listing-weekly-hours"),
]
