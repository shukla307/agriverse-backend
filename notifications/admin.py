from django.contrib import admin

from .models import DeviceToken, Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "role", "booking", "read_at", "created_at")
    list_filter = ("type", "role")
    raw_id_fields = ("user", "booking")
    readonly_fields = ("created_at",)


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    # The token itself is not shown in the list: it's an address that can receive this person's pushes.
    list_display = ("id", "user", "platform", "created_at", "last_seen_at")
    raw_id_fields = ("user",)
    readonly_fields = ("token", "created_at", "last_seen_at")
