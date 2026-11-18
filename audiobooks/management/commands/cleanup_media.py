"""Delete media files (audio, covers) not referenced by any audiobook record.

Usage:  python manage.py cleanup_media
"""
from django.core.management.base import BaseCommand

from audiobooks.media_cleanup import cleanup_unused_media


class Command(BaseCommand):
    help = "Delete unused media files, bytecode caches and stray temp files"

    def handle(self, *args, **options):
        s = cleanup_unused_media()
        self.stdout.write(
            f"Removed: {s['audio']} audio file(s), {s['covers']} cover(s), "
            f"{s['other']} other, {s['pycache_dirs']} __pycache__ dir(s)"
        )
        self.stdout.write(f"Freed: {s['bytes_freed'] / 1024:.1f} KB")
