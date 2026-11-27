"""
python manage.py run_booking_jobs   (Render Cron Job every 5 minutes, once v1 runs there; 🔑 roadmap)

  1. requests the owner didn't answer in time → expired (the same function every read and write uses lazily)
  2. accepted work that ended 12+ hours ago → completed

Safe to run as often as you like and at the same time as the app: rows in use are skipped and picked up
next time. Both leave notifications for the people concerned (slice 6); pushes come in slice 7.
"""

from django.core.management.base import BaseCommand
from django.utils import timezone

from bookings.actions import complete_done, expire_overdue
from bookings.models import Booking


class Command(BaseCommand):
    help = "Expire unanswered booking requests and complete finished work."

    def handle(self, *args, **options):
        now = timezone.now()
        expired = expire_overdue(Booking.objects.all(), now)
        completed = complete_done(now)
        self.stdout.write(f"expired {len(expired)}, completed {len(completed)}")
