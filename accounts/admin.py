from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin

from .models import Address, User


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    ordering = ("-date_joined",)
    list_display = ("phone", "name", "is_active", "is_staff", "date_joined")
    list_filter = ("is_active", "is_staff")
    search_fields = ("phone", "name")
    readonly_fields = ("firebase_uid", "date_joined", "last_seen_at", "deleted_at", "last_login")
    fieldsets = (
        (None, {"fields": ("phone", "name", "photo_url", "firebase_uid", "home_address")}),
        ("Status", {"fields": ("is_active", "deleted_at", "date_joined", "last_seen_at", "last_login")}),
        ("Admin access", {"fields": ("is_staff", "is_superuser", "password", "groups", "user_permissions")}),
    )
    add_fieldsets = ((None, {"classes": ("wide",), "fields": ("phone", "password1", "password2", "is_staff")}),)


@admin.register(Address)
class AddressAdmin(admin.ModelAdmin):
    list_display = ("user", "label", "label_other", "village", "state", "is_deleted", "created_at")
    list_filter = ("label", "is_deleted")
    search_fields = ("user__phone", "village", "label_other")
    raw_id_fields = ("user",)
