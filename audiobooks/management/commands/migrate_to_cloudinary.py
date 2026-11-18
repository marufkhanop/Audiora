"""
One-shot migration of locally stored media files to Cloudinary.

For every Audiobook whose cover/audio_file still points to a file on local
disk (uploaded before the Cloudinary integration), this command:

1. uploads the file to Cloudinary with the public_id the storage would have
   produced itself (``media/<field path without extension>``) and the
   "media" tag (so `deleteorphanedmedia` keeps matching it),
2. verifies the file is served over HTTPS,
3. repoints the DB field to the new public_id (via queryset.update(), which
   deliberately bypasses signals so the replace-file cleanup doesn't delete
   the freshly uploaded file),
4. deletes the now-migrated local copy.

It is idempotent — running it again skips files that are already on
Cloudinary or gone from local disk.
"""
import os

import cloudinary.uploader
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from cloudinary_storage.storage import MediaCloudinaryStorage, VideoMediaCloudinaryStorage

from ...models import Audiobook

FIELDS = {
    "cover": MediaCloudinaryStorage(),          # resource type: image
    "audio_file": VideoMediaCloudinaryStorage(),  # resource type: video (audio)
}


class Command(BaseCommand):
    help = "Upload local media files to Cloudinary, repoint DB fields, remove local copies."

    def add_arguments(self, parser):
        parser.add_argument(
            "--keep-local",
            action="store_true",
            help="Keep the local copies after a successful upload (default: delete them).",
        )

    def handle(self, *args, **options):
        if not getattr(settings, "CLOUDINARY_CONFIGURED", False):
            raise CommandError(
                "Cloudinary is not configured — set CLOUD_NAME / API_KEY / "
                "API_SECRET in .env first."
            )
        keep_local = options["keep_local"]

        uploaded = already = missing = failed = 0
        bytes_moved = 0

        for book in Audiobook.objects.all():
            for field, storage in FIELDS.items():
                name = getattr(book, field).name or ""
                if not name:
                    continue

                local_path = os.path.join(settings.MEDIA_ROOT, name.replace("/", os.sep))
                stem = os.path.splitext(name)[0]
                public_id = storage._prepend_prefix(stem)  # e.g. "media/audio/<uuid>"

                if storage.exists(public_id):
                    # Already on Cloudinary — just make sure the DB points at it.
                    if name != public_id:
                        Audiobook.objects.filter(pk=book.pk).update(**{field: public_id})
                    already += 1
                    continue

                if not os.path.exists(local_path):
                    self.stdout.write(self.style.WARNING(
                        f"  ? {field} of pk={book.pk}: local file {name} not found "
                        f"and not on Cloudinary — skipping."))
                    missing += 1
                    continue

                with open(local_path, "rb") as f:
                    cloudinary.uploader.upload(
                        f,
                        public_id=public_id,
                        resource_type=storage.RESOURCE_TYPE,
                        tags=[storage.TAG],
                        overwrite=True,
                        invalidate=True,
                    )

                if not storage.exists(public_id):
                    self.stdout.write(self.style.ERROR(
                        f"  ! {field} of pk={book.pk}: upload reported success but the "
                        f"file is not served yet — keeping local copy."))
                    failed += 1
                    continue

                Audiobook.objects.filter(pk=book.pk).update(**{field: public_id})
                bytes_moved += os.path.getsize(local_path)
                if not keep_local:
                    os.remove(local_path)
                uploaded += 1
                self.stdout.write(f"  + {field} of pk={book.pk}: {public_id}")

        self.stdout.write(self.style.SUCCESS(
            f"Migration finished — uploaded {uploaded} file(s) "
            f"({bytes_moved / (1024 * 1024):.1f} MB), {already} already on Cloudinary, "
            f"{missing} missing from disk, {failed} failed verification."
        ))
