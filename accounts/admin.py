from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("phone", "name", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("phone", "name")
    readonly_fields = ("firebase_uid", "date_joined", "last_seen_at", "deleted_at", "last_login")
    fieldsets = (
        (None, {"fields": ("phone", "name", "photo_url", "firebase_uid")}),
        ("Status", {"fields": ("is_active", "deleted_at", "date_joined", "last_seen_at", "last_login")}),
        ("Admin access", {"fields": ("is_staff", "is_superuser", "password", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2", "is_staff")}),)
