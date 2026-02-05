
# api/models.py
from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    """
    Custom user model: inherits from AbstractUser so username remains the unique login field.
    We add phone_number and location fields.
    """
    phone_number = models.CharField(max_length=20, unique=True, null=True, blank=True)
    location = models.CharField(max_length=255, null=True, blank=True)

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.username

