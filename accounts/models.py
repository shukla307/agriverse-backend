from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.validators import RegexValidator
from django.db import models
from django.utils import timezone

from .phone import INDIAN_MOBILE

phone_validator = RegexValidator(INDIAN_MOBILE, "Phone must look like +91XXXXXXXXXX (starting 6-9).")


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone, firebase_uid=None, **extra):
        if not phone:
            raise ValueError("phone is required")
        user = self.model(phone=phone, firebase_uid=firebase_uid, **extra)
        user.set_unusable_password()  # farmers log in with OTP only
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, password=None, **extra):
        """Admin staff only. Run: python manage.py createsuperuser"""
        if not password:
            raise ValueError("Admin users need a strong password.")
        user = self.model(phone=phone, is_staff=True, is_superuser=True, **extra)
        user.set_password(password)
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user


class User(AbstractBaseUser, PermissionsMixin):
    """
    A farmer (owner, renter or both). The phone number is the login.
    No username, and no usable password for normal users. See docs/05-data-model.md §3.1.
    """

    # null=True so a deleted account can give up its phone number (unique allows many NULLs).
    phone = models.CharField(max_length=13, unique=True, null=True, blank=True, validators=[phone_validator])
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    name = models.CharField(max_length=80, blank=True)
    photo_url = models.URLField(max_length=500, blank=True)

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    date_joined = models.DateTimeField(default=timezone.now)
    deleted_at = models.DateTimeField(null=True, blank=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    objects = UserManager()

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS: list[str] = []

    class Meta:
        verbose_name = "user"

    def __str__(self):
        return f"{self.name or '(no name)'} {self.phone or '(deleted)'}"
