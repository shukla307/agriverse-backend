from django.contrib import admin

from .models import JobCategory, MachineType


@admin.register(JobCategory)
class JobCategoryAdmin(admin.ModelAdmin):
    list_display = ("name_hi", "code", "icon", "sort_order", "is_active")
    list_editable = ("sort_order", "is_active")
    search_fields = ("code", "name_hi", "name_en")


@admin.register(MachineType)
class MachineTypeAdmin(admin.ModelAdmin):
    list_display = (
        "name_hi", "code", "kind", "fuel", "operator", "asks_transport", "attachable", "takes_attachments",
        "has_brand", "acre_price", "default_hours_per_acre", "sort_order", "is_active",
    )
    list_filter = ("kind", "fuel", "operator", "attachable", "is_active", "jobs")
    list_editable = ("sort_order", "is_active")
    search_fields = ("code", "name_hi", "name_en")
    filter_horizontal = ("jobs",)
