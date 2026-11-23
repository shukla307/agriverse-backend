from django.contrib import admin

from .models import Booking, BookingEvent


class EventInline(admin.TabularInline):
    model = BookingEvent
    extra = 0
    can_delete = False
    readonly_fields = ("from_status", "to_status", "actor", "reason", "reason_other", "created_at")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "listing", "renter", "status", "start_at", "end_at", "expires_at", "created_at")
    list_filter = ("status", "price_unit")
    raw_id_fields = ("listing", "option", "renter")
    inlines = [EventInline]
