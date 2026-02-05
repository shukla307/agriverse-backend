
from django.urls import path
from .views import EquipmentListCreateView, EquipmentSearchView, MyEquipmentDetailView, MyEquipmentListView, MyEquipmentDetailView

urlpatterns = [
    path("", EquipmentListCreateView.as_view(), name="equipment_all"),
    path("mine/", MyEquipmentListView.as_view(), name="my_equipment"),
    path("search/", EquipmentSearchView.as_view()),
    path("mine/<int:pk>/", MyEquipmentDetailView.as_view(), name="my_equipment_detail"),
]





# from django.urls import path
# from .views import EquipmentListCreateView, MyEquipmentListView

# urlpatterns = [
#     path('', EquipmentListCreateView.as_view(), name='equipment_all'),
#     path('mine/', MyEquipmentListView.as_view(), name='my_equipment'),
