from django.contrib import admin

from .models import Notification


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "type", "role", "booking", "read_at", "created_at")
    list_filter = ("type", "role")
    raw_id_fields = ("user", "booking")
    readonly_fields = ("created_at",)
