from django import forms
from django.contrib import admin, messages
from django.template.response import TemplateResponse

from catalog.models import MachineType

from .models import Listing, ListingOption, NewTypeRequest, Photo, WeeklyHours
from .moving import move_to_type, uses_other


class OptionInline(admin.TabularInline):
    model = ListingOption
    extra = 0
    raw_id_fields = ("implement",)


class PhotoInline(admin.TabularInline):
    model = Photo
    extra = 0


class WeeklyHoursInline(admin.TabularInline):
    model = WeeklyHours
    extra = 0


@admin.register(Listing)
class ListingAdmin(admin.ModelAdmin):
    list_display = ("id", "machine_type", "machine_type_other", "owner", "village", "status", "has_schedule", "created_at")
    list_filter = ("status", "has_schedule", "machine_type")
    search_fields = ("owner__phone", "owner__name", "village", "brand", "model", "machine_type_other")
    raw_id_fields = ("owner",)
    inlines = (OptionInline, PhotoInline, WeeklyHoursInline)


class MoveForm(forms.Form):
    target = forms.ModelChoiceField(
        queryset=MachineType.objects.filter(is_active=True).exclude(code="other"),
        label="Move to machine type",
    )


@admin.register(NewTypeRequest)
class NewTypeRequestAdmin(admin.ModelAdmin):
    """'New machine types to add': what owners typed after picking अन्य."""

    list_display = ("id", "typed_names", "machine_type", "owner", "village", "status", "created_at")
    search_fields = ("machine_type_other", "options__implement_other")
    actions = ("move_to_real_type",)

    def get_queryset(self, request):
        return super().get_queryset(request).filter(uses_other()).distinct().select_related("machine_type", "owner")

    def has_add_permission(self, request):
        return False

    @admin.display(description="Typed by the owner")
    def typed_names(self, obj):
        names = [obj.machine_type_other] if obj.machine_type_other else []
        names += [o.implement_other for o in obj.options.all() if o.is_active and o.implement_other]
        return " · ".join(names)

    @admin.action(description="Move to a real machine type…")
    def move_to_real_type(self, request, queryset):
        if "apply" in request.POST:
            form = MoveForm(request.POST)
            if form.is_valid():
                machines, attachments = move_to_type(list(queryset), form.cleaned_data["target"])
                self.message_user(
                    request, f"Moved {machines} machine(s) and {attachments} attachment(s).", messages.SUCCESS
                )
                return None
        else:
            form = MoveForm()
        return TemplateResponse(request, "admin/listings/move_type.html", {
            **self.admin_site.each_context(request),
            "title": "Move to a real machine type",
            "form": form,
            "listings": queryset,
            "action": "move_to_real_type",
            "opts": self.model._meta,
        })
