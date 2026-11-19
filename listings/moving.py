"""Moving "अन्य" listings to a real machine type (admin action). Kept out of admin.py so it's easy to test."""

from django.db import transaction
from django.db.models import Q

from catalog.models import MachineType

from .models import Listing, ListingOption

OTHER = "other"


def uses_other() -> Q:
    """Listings whose machine OR an active attachment is अन्य."""
    return Q(machine_type__code=OTHER) | Q(options__is_active=True, options__implement__code=OTHER)


@transaction.atomic
def move_to_type(listings, target: MachineType) -> tuple[int, int]:
    """
    - a listing whose machine is अन्य → becomes the target type (its typed name is cleared)
    - an अन्य attachment → becomes the target, but only if the target IS an attachment (kind=implement)
    Returns (machines moved, attachments moved).
    """
    if target.code == OTHER:
        raise ValueError("Pick a real machine type, not अन्य.")
    ids = [x.pk for x in listings]
    machines = Listing.objects.filter(pk__in=ids, machine_type__code=OTHER).update(
        machine_type=target, machine_type_other=""
    )
    attachments = 0
    if target.kind == MachineType.Kind.IMPLEMENT:
        for opt in ListingOption.objects.filter(listing_id__in=ids, is_active=True, implement__code=OTHER):
            clash = ListingOption.objects.filter(
                listing_id=opt.listing_id, is_active=True, implement=target, price_unit=opt.price_unit
            ).exists()
            if clash:
                # The owner already has this attachment at this unit: keep theirs, retire the duplicate.
                opt.is_active = False
                opt.save(update_fields=["is_active"])
            else:
                opt.implement, opt.implement_other = target, ""
                opt.save(update_fields=["implement", "implement_other"])
            attachments += 1
    return machines, attachments
