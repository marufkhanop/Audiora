from django.contrib import admin

from .models import PlaybackProgress


@admin.register(PlaybackProgress)
class PlaybackProgressAdmin(admin.ModelAdmin):
    list_display = ("user", "book", "position", "completed", "updated_at")
    list_filter = ("completed", "updated_at")
    search_fields = ("user__username", "user__email", "book__title")
    autocomplete_fields = ("book",)
    raw_id_fields = ("user",)
    date_hierarchy = "updated_at"
