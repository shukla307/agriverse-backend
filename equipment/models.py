

from django.db import models
from django.conf import settings

User = settings.AUTH_USER_MODEL

class Equipment(models.Model):
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="equipments"
    )

    equipment_name = models.CharField(max_length=150)
    price_per_day = models.DecimalField(max_digits=10, decimal_places=2)

    description = models.TextField(blank=True)

    contact_number = models.CharField(
        max_length=15,
        blank=True,
        null=True
    )

    location = models.CharField(
        max_length=255,
        blank=True,
        null=True
    )

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
#new ksz
    image_url = models.URLField(null=True, blank=True)



    available = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return self.equipment_name



# old code 



# from django.db import models
# from django.conf import settings

# User = settings.AUTH_USER_MODEL

# class Equipment(models.Model):
#     owner = models.ForeignKey(
#         User,
#         on_delete=models.CASCADE,
#         related_name="equipments"
#     )

#     equipment_name = models.CharField(max_length=150)
#     price_per_day = models.DecimalField(max_digits=10, decimal_places=2)

#     description = models.TextField(blank=True)

#     contact_number = models.CharField(
#         max_length=15,
#         blank=True,
#         null=True
#     )

#     location = models.CharField(
#         max_length=255,
#         blank=True,
#         null=True
#     )

#     available = models.BooleanField(default=True)

#     created_at = models.DateTimeField(auto_now_add=True)

#     def __str__(self):
#         return self.equipment_name

# # Create your models here.
