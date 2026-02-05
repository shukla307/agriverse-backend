# api/views.py

from django.shortcuts import render

from rest_framework import generics, permissions, status
from rest_framework.response import Response
from .serializers import RegisterSerializer
from django.contrib.auth import get_user_model
from rest_framework.decorators import api_view

from rest_framework.views import APIView
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from .serializers import RegisterSerializer, LoginSerializer, FirebaseLoginSerializer
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import RefreshToken
from core.firebase import verify_firebase_token
 # assumes core/firebase.py exists and init done
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from .serializers import ForgotPasswordSerializer




User = get_user_model()

def get_tokens_for_user(user):
    r = RefreshToken.for_user(user)
    return {"refresh": str(r), "access": str(r.access_token)}

class RegisterView(generics.CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.save()
        return Response({"id":user.id,"username":user.username}, status=status.HTTP_201_CREATED)

class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        ser = LoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        user = ser.validated_data['user']
        tokens = get_tokens_for_user(user)
        return Response({"user":{"id":user.id,"username":user.username,"phone_number":user.phone_number}, "tokens": tokens})

class FirebaseLoginView(APIView):
    permission_classes = [permissions.AllowAny]
    def post(self, request):
        ser = FirebaseLoginSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        token = ser.validated_data['firebase_token']
        try:
            decoded = verify_firebase_token(token)
        except Exception as e:
            return Response({"detail":f"Invalid firebase token: {e}"}, status=status.HTTP_400_BAD_REQUEST)
        phone = decoded.get('phone_number')
        uid = decoded.get('uid')
        user = None
        if phone:
            user = User.objects.filter(phone_number=phone).first()
        if not user:
            # create user if username provided
            username = ser.validated_data.get('username')
            if not username:
                return Response({"detail":"User not found. Provide username to create new user."}, status=status.HTTP_400_BAD_REQUEST)
            if User.objects.filter(username=username).exists():
                return Response({"detail":"Username already exists."}, status=status.HTTP_400_BAD_REQUEST)
            user = User.objects.create(username=username,
                                       first_name=ser.validated_data.get('first_name',''),
                                       last_name=ser.validated_data.get('last_name',''),
                                       phone_number=phone,
                                       location=ser.validated_data.get('location',''))
            user.set_unusable_password()
            user.save()
        tokens = get_tokens_for_user(user)
        return Response({"user": {"id":user.id,"username":user.username,"phone_number":user.phone_number}, "tokens": tokens})



class FirebaseResetPassword(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        """
        Expects JSON:
        {
          "firebase_token": "<Firebase ID token>",
          "new_password": "NewPass123",
          "confirm_new_password": "NewPass123"
        }

        Steps:
        - verify firebase id token with firebase-admin
        - extract phone number from token
        - find user by phone_number
        - set new password and save
        """
        token = request.data.get("firebase_token")
        npw = request.data.get("new_password")
        cpw = request.data.get("confirm_new_password")

        if not token:
            return Response({"detail": "firebase_token required."}, status=status.HTTP_400_BAD_REQUEST)
        if not npw or not cpw:
            return Response({"detail": "Provide new_password and confirm_new_password."}, status=status.HTTP_400_BAD_REQUEST)
        if npw != cpw:
            return Response({"detail": "Passwords do not match."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            decoded = verify_firebase_token(token)
        except Exception as e:
            return Response({"detail": f"Invalid firebase token: {e}"}, status=status.HTTP_400_BAD_REQUEST)

        phone = decoded.get("phone_number")
        if not phone:
            return Response({"detail": "Token does not contain phone number."}, status=status.HTTP_400_BAD_REQUEST)

        user = User.objects.filter(phone_number=phone).first()
        if not user:
            return Response({"detail": "No user found for this phone number."}, status=status.HTTP_404_NOT_FOUND)

        user.set_password(npw)
        user.save()

        return Response({"detail": "Password updated successfully."})




# class MeView(APIView):
#     permission_classes = [IsAuthenticated]

#     def get(self, request):
#         user = request.user
#         return Response({
#             "id": user.id,
#             "username": user.username,
#             "phone_number": user.phone_number,
#             "first_name": user.first_name,
#             "last_name": user.last_name,
#             "location": user.location,
#         })


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        user = request.user
        return Response({
            "id": user.id,
            "username": user.username,
            "phone_number": user.phone_number,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "location": user.location,
            "latitude": user.latitude,
            "longitude": user.longitude,
        })


#for later use : check if phone number exists
 
class CheckPhoneExists(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, *args, **kwargs):
        phone = request.data.get('phone_number')
        if not phone:
            return Response({"detail": "phone_number required."}, status=status.HTTP_400_BAD_REQUEST)

        exists = User.objects.filter(phone_number=phone).exists()
        if exists:
            return Response({"detail": "phone registered"}, status=status.HTTP_200_OK)
        else:
            return Response({"detail": "phone not registered"}, status=status.HTTP_404_NOT_FOUND)


#new ksz
class ForgotPasswordView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        user = serializer.validated_data["user"]
        new_password = serializer.validated_data["new_password"]

        user.set_password(new_password)
        user.save()

        return Response(
            {"detail": "Password updated successfully."},
            status=status.HTTP_200_OK
        )


# class FirebaseResetPassword(APIView):
#     permission_classes = [permissions.AllowAny]
#     @api_view(['POST'])
#     def firebase_reset_password(request):
#         # expects {"firebase_token": "...", "new_password":"...","confirm_new_password":"..."}
#         token = request.data.get('firebase_token')
#         npw = request.data.get('new_password')
#         cpw = request.data.get('confirm_new_password')
#         if not token:
#             return Response({"detail":"firebase_token required."}, status=400)
#         if not npw or not cpw or npw!=cpw:
#             return Response({"detail":"Password fields mismatch."}, status=400)
#         try:
#             decoded = verify_firebase_token(token)
#         except Exception as e:
#             return Response({"detail":f"Invalid token: {e}"}, status=400)
#         phone = decoded.get('phone_number')
#         if not phone:
#             return Response({"detail":"Token does not contain phone number."}, status=400)
#         user = User.objects.filter(phone_number=phone).first()
#         if not user:
#             return Response({"detail":"No user for this phone."}, status=404)
#         user.set_password(npw)
#         user.save()
#         return Response({"detail":"Password updated."})



    
    


# class RegisterView(generics.CreateAPIView):
#     serializer_class = RegisterSerializer
#     permission_classes = [permissions.AllowAny]

#     def create(self, request, *args, **kwargs):
#         """
#         Overriding to provide custom response structure and status code.
#         """
#         serializer = self.get_serializer(data=request.data)
#         serializer.is_valid(raise_exception=True)
#         user = serializer.save()
#         data = {
#             "id": user.id,
#             "username": user.username,
#             "first_name": user.first_name,
#             "last_name": user.last_name,
#             "phone_number": user.phone_number,
#             "location": user.location,
#             "detail": "User registered successfully."
#         }
#         return Response(data, status=status.HTTP_201_CREATED)

# # Create your views here.
