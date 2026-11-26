"""
Fake listings for speed checks on the LOCAL dev database (roadmap Phase 4: search < 200 ms with 1,000 listings).

    python manage.py seed_listings --count 1000          # around Prayagraj, up to 60 km out
    python manage.py seed_listings --delete              # removes everything this command made

Every fake owner has firebase_uid "seed-…" and a +91 6000… phone, so --delete finds exactly them (listings go
with them, CASCADE). Refuses to run with DEBUG off, so it can't touch production by mistake.
"""

import math
import random
from decimal import Decimal

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounts.models import User
from catalog.models import MachineType
from listings.models import Listing, ListingOption, Photo, WeeklyHours

SEED_PREFIX = "seed-"
PRAYAGRAJ = (25.4358, 81.8463)


class Command(BaseCommand):
    help = "Create (or --delete) fake listings for speed checks. Dev database only."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=1000)
        parser.add_argument("--spread-km", type=float, default=60)
        parser.add_argument("--delete", action="store_true")

    def handle(self, *args, count, spread_km, delete, **_):
        if not settings.DEBUG:
            raise CommandError("Only with DEBUG=True (local dev database).")
        if delete:
            n, _ = User.objects.filter(firebase_uid__startswith=SEED_PREFIX).delete()
            self.stdout.write(f"Deleted {n} rows made by seed_listings.")
            return

        rng = random.Random(42)
        power = list(MachineType.objects.filter(kind="power", is_active=True))
        implements = list(MachineType.objects.filter(kind="implement", is_active=True))
        attachable = list(MachineType.objects.filter(attachable=True, is_active=True))
        start = User.objects.filter(firebase_uid__startswith=SEED_PREFIX).count()
        with transaction.atomic():
            owners = User.objects.bulk_create(
                User(phone=f"+916000{start + i:06d}", firebase_uid=f"{SEED_PREFIX}{start + i}", name=f"Seed {i}")
                for i in range(max(1, count // 3))
            )
            listings = []
            for i in range(count):
                km, bearing = spread_km * math.sqrt(rng.random()), rng.uniform(0, 360)  # even over the area
                lat = PRAYAGRAJ[0] + km / 111.045 * math.cos(math.radians(bearing))
                lng = PRAYAGRAJ[1] + km / (111.045 * math.cos(math.radians(PRAYAGRAJ[0]))) * math.sin(
                    math.radians(bearing))
                standalone = rng.random() < 0.3
                machine = rng.choice(implements if standalone else power)
                listings.append(Listing(
                    owner=rng.choice(owners),
                    machine_type=machine,
                    # Fixed terms (decision §22): fuel and transport included where they apply, gap 1 hour.
                    fuel_included=True if machine.fuel not in ("battery", "none") else None,
                    transport_included=True if machine.asks_transport else None,
                    gap_hours=Listing.GAP_HOURS,
                    lat=Decimal(f"{lat:.6f}"), lng=Decimal(f"{lng:.6f}"), village=f"गाँव {i}",
                    hp=None if standalone else rng.choice([None, 30, 45, 55]),
                    has_schedule=True,  # every new machine gets hours 7–19 (decision §22)
                    status=Listing.Status.ACTIVE if rng.random() < 0.9 else Listing.Status.PAUSED,
                ))
            Listing.objects.bulk_create(listings)
            options, photos, hours = [], [], []
            for listing in listings:
                # One unit per machine (decision §22): hour, or acre for some tractors.
                unit = "acre" if listing.machine_type.takes_attachments and rng.random() < 0.3 else "hour"
                options.append(ListingOption(listing=listing, price_unit=unit, price=Decimal(rng.randrange(400, 1200, 50)),
                                             hours_per_acre=Decimal("1.5") if unit == "acre" else None))
                # Only tractors pull attachments (catalogue setting takes_attachments).
                if listing.machine_type.takes_attachments:
                    usable = [a for a in attachable if unit != "acre" or a.acre_price]
                    for imp in rng.sample(usable, k=rng.randint(0, 3)):
                        options.append(ListingOption(listing=listing, implement=imp, price_unit=unit,
                                                     price=Decimal(rng.randrange(800, 2500, 100))))
                for s in range(rng.randint(0, 3)):
                    photos.append(Photo(listing=listing, url="https://example.com/f.jpg",
                                        thumb_url="https://example.com/t.jpg", storage_path=f"seed/{listing.pk}/{s}",
                                        sort_order=s))
                if listing.has_schedule:
                    start, end = Listing.DEFAULT_HOURS
                    hours += [WeeklyHours(listing=listing, weekday=d, start_hour=start, end_hour=end) for d in range(7)]
            ListingOption.objects.bulk_create(options)
            Photo.objects.bulk_create(photos)
            WeeklyHours.objects.bulk_create(hours)
        self.stdout.write(f"Created {count} listings ({len(options)} prices, {len(photos)} photos) "
                          f"for {len(owners)} fake owners.")
