from django.apps import AppConfig


class AudiobooksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "audiobooks"

    def ready(self):
        # Keep every storage backend in sync with the DB (delete local/cloud
        # files when records are deleted or their media is replaced).
        from . import signals  # noqa: F401
