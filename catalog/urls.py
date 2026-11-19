from django.urls import path

from .views import MachineTypesView

urlpatterns = [
    path("machine-types/", MachineTypesView.as_view(), name="machine-types"),
]
