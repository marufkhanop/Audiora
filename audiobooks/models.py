import io
import os
import uuid

from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.core.validators import FileExtensionValidator
from django.db import models
from django.utils.text import slugify
from mutagen import File as MutagenFile

AUDIO_MAX_BYTES = 250 * 1024 * 1024   # 250 MB
IMAGE_MAX_BYTES = 10 * 1024 * 1024    # 10 MB
AUDIO_EXTENSIONS = ["mp3", "m4a", "aac", "ogg", "wav"]
COVER_EXTENSIONS = ["jpg", "jpeg", "png", "webp"]

LOCAL_BACKEND_NAME = "Local Server Storage"

# Media file fields of Audiobook and their immutable local (default) storages.
MEDIA_FIELDS = ("cover", "audio_file")
LOCAL_FIELD_STORAGES = {
    "cover": FileSystemStorage(),
    "audio_file": FileSystemStorage(),
}
# Kept as an alias for older imports: the default is now local disk.
DEFAULT_FIELD_STORAGES = LOCAL_FIELD_STORAGES
BOUND_FIELD_STORAGES = {
    "cover": "AccountBoundMediaStorage",
    "audio_file": "AccountBoundVideoStorage",
}


def _bound_storage(field_name, account):
    """Import Cloudinary bindings lazily so local-only installs boot cleanly."""
    from .multi_cloud import AccountBoundMediaStorage, AccountBoundVideoStorage

    cls = {
        "cover": AccountBoundMediaStorage,
        "audio_file": AccountBoundVideoStorage,
    }[field_name]
    return cls(account)


class CloudStorageAccount(models.Model):
    """A storage backend: the protected local disk or a Cloudinary account.

    Every installation owns exactly one protected ``local`` row (created by
    migration). It is the permanent default, stores files under MEDIA_ROOT and
    can never be renamed or deleted. Additional rows represent Cloudinary
    accounts added by an administrator; they are bound per upload so multiple
    clouds can be used side by side.

    Deleting a Cloudinary account wipes every file stored on it and removes the
    audiobooks that point at it (see `delete`).
    """

    KIND_LOCAL = "local"
    KIND_CLOUDINARY = "cloudinary"
    KIND_CHOICES = [
        (KIND_LOCAL, "Local server storage"),
        (KIND_CLOUDINARY, "Cloudinary"),
    ]

    name = models.CharField(max_length=80, unique=True)
    kind = models.CharField(
        max_length=20,
        choices=KIND_CHOICES,
        default=KIND_CLOUDINARY,
        db_index=True,
    )
    cloud_name = models.CharField(max_length=80, unique=True, blank=True)
    api_key = models.CharField(max_length=64, blank=True)
    api_secret = models.CharField(max_length=64, blank=True)
    is_protected = models.BooleanField(
        default=False,
        help_text="System backends cannot be renamed or deleted.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Storage backend"
        verbose_name_plural = "Storage backends"

    def __str__(self):
        return self.name if self.is_local else f"{self.name} ({self.cloud_name})"

    @classmethod
    def local(cls):
        """Return (creating if needed) the immutable local storage row."""
        account = cls.objects.filter(kind=cls.KIND_LOCAL).order_by("pk").first()
        if account is None:
            account = cls.objects.create(
                name=LOCAL_BACKEND_NAME,
                kind=cls.KIND_LOCAL,
                is_protected=True,
            )
        return account

    @property
    def is_local(self):
        return self.kind == self.KIND_LOCAL

    def verify(self):
        """Contact Cloudinary to confirm the credentials work. Raises on failure."""
        if self.is_local:
            return {"status": "ok", "local": True}
        from .multi_cloud import ping_account

        return ping_account(self)

    def usage(self):
        """Admin API usage report for this account (raises on API errors)."""
        from .multi_cloud import account_usage

        return account_usage(self)

    def wipe_media(self):
        """Delete every uploaded file on this account. See multi_cloud."""
        if self.is_local:
            return {"deleted": 0, "already_gone": False}
        from .multi_cloud import wipe_account_media

        return wipe_account_media(self)

    def save(self, *args, **kwargs):
        if self.is_local:
            # The system backend keeps its identity no matter what a caller
            # tries to assign. The admin also hides/denies these fields.
            self.name = LOCAL_BACKEND_NAME
            self.is_protected = True
            self.cloud_name = ""
            self.api_key = ""
            self.api_secret = ""
        super().save(*args, **kwargs)

    def delete(self, using=None, keep_parents=False):
        if self.is_local or self.is_protected:
            from django.db.models.deletion import ProtectedError

            raise ProtectedError(
                "Local Server Storage is the permanent default backend and "
                "cannot be deleted.",
                set(),
            )
        # Wipe the cloud first; if it fails (network), nothing in the DB is
        # touched and the caller can retry. DB rows cascade afterwards.
        # The wipe stats are stashed here because Django's own delete()
        # return value is the standard (total, per-model) cascade tuple.
        self.wipe_result = self.wipe_media()
        return super().delete(using=using, keep_parents=keep_parents)

    wipe_result = None


def audiobook_cover_path(instance, filename):
    ext = filename.split(".")[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    return os.path.join("covers/", filename)


def audiobook_file_path(instance, filename):
    ext = filename.split(".")[-1]
    filename = f"{uuid.uuid4().hex}.{ext}"
    return os.path.join("audio/", filename)


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True)
    description = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name_plural = "Categories"
        ordering = ["name"]

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Audiobook(models.Model):
    title = models.CharField(max_length=255, db_index=True)
    slug = models.SlugField(max_length=280, unique=True, blank=True)
    author = models.CharField(max_length=255, db_index=True)
    narrator = models.CharField(max_length=255, db_index=True)
    description = models.TextField()
    storage_account = models.ForeignKey(
        CloudStorageAccount,
        on_delete=models.CASCADE,
        related_name="audiobooks",
        help_text="Backend this book's files live on: Local Storage (default) "
                  "or a named Cloudinary account. Locked after upload — delete "
                  "the book to move its files.",
    )
    # Explicit, query-friendly copy of the chosen backend: "local" or the
    # Cloudinary account's database ID. Kept in sync by save().
    storage_backend = models.CharField(
        max_length=64,
        default="local",
        editable=False,
        db_index=True,
        help_text="'local' or the ID of the Cloudinary storage account.",
    )
    cover = models.ImageField(
        upload_to=audiobook_cover_path,
        storage=FileSystemStorage(),  # default backend: MEDIA_ROOT on this server
    )
    audio_file = models.FileField(
        upload_to=audiobook_file_path,
        storage=FileSystemStorage(),  # default backend: MEDIA_ROOT on this server
        validators=[FileExtensionValidator(allowed_extensions=AUDIO_EXTENSIONS)],
    )
    duration = models.PositiveIntegerField(
        default=0, help_text="Duration in seconds (auto-calculated if 0)"
    )
    category = models.ForeignKey(
        Category, on_delete=models.SET_NULL, null=True, related_name="audiobooks"
    )
    published = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def _file_size(self, field_name):
        """Size of the field's file from the correct storage (0 when absent)."""
        field_file = getattr(self, field_name)
        if not field_file:
            return 0
        if not field_file._committed:
            return field_file.file.size
        try:
            return self.field_storage(field_name).size(field_file.name) or 0
        except Exception:
            return 0

    def clean(self):
        from django.core.exceptions import ValidationError

        if self._file_size("audio_file") > AUDIO_MAX_BYTES:
            raise ValidationError({"audio_file": "Audio files may not exceed 250 MB."})
        if self._file_size("cover") > IMAGE_MAX_BYTES:
            raise ValidationError({"cover": "Cover images may not exceed 10 MB."})

    # ---------- Storage routing (hybrid local / multi-account) ----------

    def field_storage(self, field_name):
        """The storage this book's files live on (its account, or local disk)."""
        account = self.storage_account
        if account is None or account.is_local:
            return LOCAL_FIELD_STORAGES[field_name]
        cache = getattr(self, "_bound_storages", None)
        if cache is None:
            cache = self._bound_storages = {}
        storage = cache.get(field_name)
        if storage is None:
            storage = _bound_storage(field_name, account)
            cache[field_name] = storage
        return storage

    @property
    def storage_source(self):
        """The explicit backend reference stored on this record."""
        return self.storage_backend

    def _save_pending_files(self):
        """Upload any un-committed file fields through the correct storage.

        Replaces Django's own FileField.pre_save so a book can direct its
        uploads to the storage account chosen in the admin form. Must be
        called before super().save().
        """
        for field_name in MEDIA_FIELDS:
            field_file = getattr(self, field_name)
            if field_file and not field_file._committed:
                storage = self.field_storage(field_name)
                # Apply upload_to (covers/<uuid>, audio/<uuid>) ourselves;
                # Django's FileField.pre_save normally does this, but we
                # replace it to route the upload to the chosen backend.
                name = field_file.field.generate_filename(self, field_file.name)
                field_file.name = storage.save(
                    name, field_file.file,
                    max_length=getattr(field_file.field, "max_length", None),
                )
                field_file._committed = True

    @property
    def cover_url(self):
        """HTTPS URL of the cover on its storage account ('' when absent)."""
        if not self.cover:
            return ""
        try:
            return self.field_storage("cover").url(self.cover.name)
        except Exception:
            return ""

    @property
    def audio_url(self):
        """HTTPS URL of the audio on its storage account ('' when absent)."""
        if not self.audio_file:
            return ""
        try:
            return self.field_storage("audio_file").url(self.audio_file.name)
        except Exception:
            return ""

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:275] or "audiobook"
            slug, suffix = base, 0
            while Audiobook.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                suffix += 1
                slug = f"{base}-{suffix}"
            self.slug = slug

        # Every record must always reference a backend. New books without an
        # explicit destination default to the protected local storage row.
        if not self.storage_account_id:
            self.storage_account = CloudStorageAccount.local()
        self.storage_backend = (
            "local" if self.storage_account.is_local else str(self.storage_account_id)
        )

        # Auto-extract duration from the uploaded audio using Mutagen.
        # Careful: never close the underlying upload file — Django saves it
        # to storage afterwards (FieldFile pre_save -> storage.save).
        if self.audio_file and self.duration == 0:
            self._extract_duration()
        # Route pending uploads to the storage account chosen for this book
        # (before super().save(), so Django sees them as already committed).
        self._save_pending_files()
        super().save(*args, **kwargs)

    def _extract_duration(self):
        try:
            field_file = self.audio_file
            raw = field_file.file if hasattr(field_file, "file") else field_file
            if getattr(raw, "closed", False):
                # Committed/stored file: open a fresh handle from storage.
                raw = self.field_storage("audio_file").open(field_file.name, "rb")
                owned = True
            else:
                owned = False  # live upload — Django still needs it open
            restore = raw.tell()
            raw.seek(0)
            try:
                # Note: mutagen FileType objects can be falsy for tag-less
                # files, so never use `audio and ...` — check for None.
                audio = MutagenFile(raw)
                if not (audio is not None and audio.info is not None
                        and hasattr(audio.info, "length")):
                    # Fallback: buffer the whole file (handles short reads
                    # on filesystems like Android FUSE).
                    raw.seek(0)
                    audio = MutagenFile(io.BytesIO(raw.read()))
                if (audio is not None and audio.info is not None
                        and hasattr(audio.info, "length")):
                    self.duration = int(audio.info.length)
            finally:
                raw.seek(restore)
                if owned:
                    raw.close()
        except Exception:
            pass  # Fall back gracefully; duration stays at its current value

    @property
    def formatted_duration(self):
        hours = self.duration // 3600
        minutes = (self.duration % 3600) // 60
        if hours > 0:
            return f"{hours} hr {minutes} min"
        return f"{minutes} min"

    @property
    def duration_hms(self):
        hours, rem = divmod(self.duration, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    def __str__(self):
        return f"{self.title} - {self.author}"


class SavedBook(models.Model):
    """A listener's bookmark: the books they saved for later."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="saved_books",
    )
    audiobook = models.ForeignKey(
        Audiobook,
        on_delete=models.CASCADE,
        related_name="saved_by",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "audiobook"], name="unique_saved_book_per_user"
            )
        ]
        verbose_name = "Saved audiobook"
        verbose_name_plural = "Saved audiobooks"

    def __str__(self):
        return f"{self.user} saved {self.audiobook}"
