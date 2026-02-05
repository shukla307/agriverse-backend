# api/serializers.py

from rest_framework import serializers
from django.contrib.auth import authenticate, password_validation, get_user_model
from rest_framework import serializers
from django.contrib.auth import get_user_model, password_validation

User = get_user_model()
import re

User = get_user_model()
PHONE_REGEX = re.compile(r'^\+?\d{7,15}$')

User = get_user_model()
PHONE_REGEX = re.compile(r'^\+?\d{7,15}$')


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=4, required=True)
    confirm_password = serializers.CharField(write_only=True, min_length=4, required=True)
    latitude = serializers.FloatField(required=False)
    longitude = serializers.FloatField(required=False)

    class Meta:
        model = User
        fields = ('username', 'first_name', 'last_name', 'phone_number', 'location', 'password', 'confirm_password', 'latitude', 'longitude')

    def validate_username(self, value):
        if User.objects.filter(username=value).exists():
            raise serializers.ValidationError("Username already exists.")
        return value

    def validate_phone_number(self, value):
        if value and not PHONE_REGEX.match(value):
            raise serializers.ValidationError("Phone number format invalid. Use +countrycodeXXXXXXXX")
        if value and User.objects.filter(phone_number=value).exists():
            raise serializers.ValidationError("Phone number already in use.")
        return value

    def validate(self, data):
        # password match
        if data.get('password') != data.get('confirm_password'):
            raise serializers.ValidationError({"password": "Passwords do not match."})

        # use Django's password validators; pass a minimal user object to avoid unexpected kwargs
        # create a minimal user for validation context (only fields used by validators)
        dummy_user = User(username=data.get('username') or "")
        # call Django's validators; this may raise ValidationError which DRF will convert
        password_validation.validate_password(data['password'], user=dummy_user)

        return data

    def create(self, validated_data):
        validated_data.pop('confirm_password', None)
        password = validated_data.pop('password')
        # create user with remaining fields
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        return user



class LoginSerializer(serializers.Serializer):
    identifier = serializers.CharField(required=True)  # username or phone
    password = serializers.CharField(write_only=True, required=True)

    def validate(self, data):
        ident = data['identifier'].strip()
        pwd = data['password']
        # find user by username or phone
        user = None
        if User.objects.filter(username=ident).exists():
            user = User.objects.get(username=ident)
        elif User.objects.filter(phone_number=ident).exists():
            user = User.objects.get(phone_number=ident)
        else:
            raise serializers.ValidationError("Invalid credentials.")
        user = authenticate(username=user.username, password=pwd)
        if not user:
            raise serializers.ValidationError("Invalid credentials.")
        data['user'] = user
        return data

#newdata ksz

class ForgotPasswordSerializer(serializers.Serializer):
    phone_number = serializers.CharField()
    new_password = serializers.CharField(write_only=True)
    confirm_password = serializers.CharField(write_only=True)

    def validate(self, data):
        if data["new_password"] != data["confirm_password"]:
            raise serializers.ValidationError({
                "password": "Passwords do not match."
            })

        user = User.objects.filter(phone_number=data["phone_number"]).first()
        if not user:
            raise serializers.ValidationError({
                "phone_number": "Phone number not registered."
            })

        # Django password validation 
        password_validation.validate_password(
            data["new_password"],
            user=user
        )

        data["user"] = user
        return data


# class FirebaseLoginSerializer(serializers.Serializer):
#     firebase_token = serializers.CharField(required=True)
#     username = serializers.CharField(required=False, allow_blank=True)
#     first_name = serializers.CharField(required=False, allow_blank=True)
#     last_name = serializers.CharField(required=False, allow_blank=True)
#     location = serializers.CharField(required=False, allow_blank=True)
#     phone_number = serializers.CharField(required=False, allow_blank=True)



