
from rest_framework import generics, permissions
from .models import Equipment
from .serializers import EquipmentSerializer
from rest_framework.permissions import IsAuthenticated
from math import radians, cos, sin, asin, sqrt
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny
from django.core.paginator import Paginator

# class EquipmentListCreateView(generics.ListCreateAPIView):
#     serializer_class = EquipmentSerializer
#     permission_classes = [permissions.IsAuthenticated]

#     def get_queryset(self):
#         return Equipment.objects.all().order_by("-created_at")

#     def perform_create(self, serializer):
#         user = self.request.user

#         contact = serializer.validated_data.get("contact_number") or user.phone_number
#         location = serializer.validated_data.get("location") or user.location

#         serializer.save(
#             owner=user,
#             contact_number=contact,
#             location=location
#         )


class EquipmentListCreateView(generics.ListCreateAPIView):
    serializer_class = EquipmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = Equipment.objects.all().order_by("-created_at")

        search = self.request.query_params.get("search")
        if search:
            queryset = queryset.filter(equipment_name__icontains=search)

        return queryset

    # def perform_create(self, serializer):
    #     user = self.request.user
    #     contact = serializer.validated_data.get("contact_number") or user.phone_number
    #     location = serializer.validated_data.get("location") or user.location
    #     serializer.save(owner=user, contact_number=contact, location=location)


    def perform_create(self, serializer):
        user = self.request.user
        serializer.save(
            owner=user,
            contact_number=serializer.validated_data.get("contact_number") or user.phone_number,
            location=serializer.validated_data.get("location") or user.location,
            latitude=user.latitude,
            longitude=user.longitude,
            available=True,
        )


class MyEquipmentListView(generics.ListAPIView):
    serializer_class = EquipmentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Equipment.objects.filter(owner=self.request.user)



class MyEquipmentDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = EquipmentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        # Only allow user to edit/delete their own equipment
        return Equipment.objects.filter(owner=self.request.user)


# Utility function to calculate distance between two lat/lon points




def haversine(lat1, lon1, lat2, lon2):
    R = 6371  # Earth radius in KM
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    print(lat1, lon1, lat2, lon2)
    print("Calculating distance... lat1:", lat1, "lon1:", lon1, "lat2:", lat2, "lon2:", lon2)

    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    print("Intermediate calculations: dlat:", dlat, "dlon:", dlon, "a:", a, "c:", c)
    return R * c


class EquipmentSearchView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        query = request.GET.get("q", "").strip().lower()
        max_distance = float(request.GET.get("distance", 10))
        page = int(request.GET.get("page", 1))

        user_lat = request.GET.get("lat")
        user_lng = request.GET.get("lng")

        if not user_lat or not user_lng:
            return Response({"detail": "lat and lng required"}, status=400)

        user_lat = float(user_lat)
        user_lng = float(user_lng)

        #ase queryset
        equipments = Equipment.objects.filter(available=True)

        # Filter by name
        if query:
            equipments = equipments.filter(equipment_name__icontains=query)

        if not equipments.exists():
            return Response({
                "message": "No equipment found",
                "results": []
            })

        within_range = []
        outside_range = []

        # Distance calculation
        for eq in equipments:
            if eq.latitude is None or eq.longitude is None:
                continue

            dist = haversine(user_lat, user_lng, eq.latitude, eq.longitude)

            data = {
                "id": eq.id,
                "equipment_name": eq.equipment_name,
                "price_per_day": eq.price_per_day,
                "distance_km": round(dist, 2),
                "available": eq.available,
                "contact_number": eq.contact_number,
                "description": eq.description,
                "location": eq.location,
                "image_url": eq.image_url,
                "owner_username": eq.owner.username,
                "first_name": eq.owner.first_name,
                "last_name": eq.owner.last_name,
            }

            if dist <= max_distance:
                within_range.append(data)
            else:
                outside_range.append((dist, data))

        # Decide response set
        if within_range:
            results = sorted(within_range, key=lambda x: x["distance_km"])
            message = None
        else:
            # nearest fallback
            outside_range.sort(key=lambda x: x[0])
            results = [item[1] for item in outside_range]
            message = f"No equipment within {max_distance} km. Showing nearest available."

        # Pagination (5 per page)
        paginator = Paginator(results, 7)

        # Get requested page ksz 
        page_number = request.GET.get('page')

        #page_obj = paginator.get_page(page)

        page_obj = paginator.get_page(page_number)
        print("Page object:", page_obj)
       # print("Page object list:", page_obj.object_list)



        # All_post = Post.objects.all()
        # (All no of  list)

        # paginator = Paginator(All_post, 5)

        # page_number = request.Get.get(‘page’)

        # page_obj = paginator.get_page(page_number)

        
        return Response({
            "message": message,
            "page": page,
            "total_pages": paginator.num_pages,
            "total_results": paginator.count,
            "results": page_obj.object_list,
        })


#new search view




