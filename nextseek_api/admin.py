from django.contrib import admin

from .seek_link import SeekPersonLink


@admin.register(SeekPersonLink)
class SeekPersonLinkAdmin(admin.ModelAdmin):
    list_display = ("user", "person_id", "linked_at")
    raw_id_fields = ("user",)
