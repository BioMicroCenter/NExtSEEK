"""Which SEEK person a NExtSEEK (Django) account belongs to.

A SEEK login may create or update a Django account only through this link, so a SEEK user who picks the name of an
unrelated account (an operator's, say) cannot take it over.
"""
from django.conf import settings
from django.db import models
from django.utils import timezone


class SeekPersonLink(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, primary_key=True)
    person_id = models.IntegerField(unique=True)
    linked_at = models.DateTimeField(default=timezone.now)

    class Meta:
        app_label = "nextseek_api"

    def __str__(self):
        return f"{self.user_id} -> SEEK person {self.person_id}"
