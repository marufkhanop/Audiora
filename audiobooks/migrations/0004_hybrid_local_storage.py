import audiobooks.models
import django.core.validators
from django.conf import settings
from django.core.files.storage import FileSystemStorage
from django.db import migrations, models

LOCAL_NAME = "Local Server Storage"
LEGACY_NAME = "Default Cloudinary (legacy)"


def _create_default_backends(apps, schema_editor):
    """Create the protected local row and preserve books already on Cloudinary.

    Before this migration every audiobook with a NULL storage_account lived on
    the .env Cloudinary account. Those files are still there, so we import that
    account as a normal named backend and point the existing rows at it. New
    uploads default to the protected local row.
    """
    Account = apps.get_model("audiobooks", "CloudStorageAccount")
    Book = apps.get_model("audiobooks", "Audiobook")

    local = Account.objects.filter(kind="local").order_by("pk").first()
    if local is None:
        name = LOCAL_NAME
        if Account.objects.filter(name=name).exists():
            name = f"{LOCAL_NAME} (System)"
        local = Account.objects.create(
            name=name, kind="local", is_protected=True
        )
    else:
        local.name = LOCAL_NAME
        local.is_protected = True
        local.save(update_fields=["name", "is_protected"])

    legacy = None
    pending = Book.objects.filter(storage_account__isnull=True)
    cloud_name = getattr(settings, "CLOUDINARY_CLOUD_NAME", "") or ""
    api_key = getattr(settings, "CLOUDINARY_API_KEY", "") or ""
    api_secret = getattr(settings, "CLOUDINARY_API_SECRET", "") or ""
    # Only import the .env account when existing rows actually need it (this
    # keeps a brand-new installation local-only). Fresh uploads always start
    # on the protected local backend.
    if pending.exists() and cloud_name and api_key and api_secret:
        legacy = Account.objects.filter(cloud_name=cloud_name).first()
        if legacy is None:
            name = LEGACY_NAME
            if Account.objects.filter(name=name).exists():
                name = f"{LEGACY_NAME} {cloud_name}"[:80]
            legacy = Account.objects.create(
                name=name,
                kind="cloudinary",
                cloud_name=cloud_name,
                api_key=api_key,
                api_secret=api_secret,
            )

    target = legacy or local
    pending.update(
        storage_account=target,
        storage_backend="local" if target.kind == "local" else str(target.pk),
    )

    # Keep the explicit denormalised reference in sync for every existing row.
    for book in Book.objects.select_related("storage_account"):
        account = book.storage_account
        if account is None:
            continue
        book.storage_backend = (
            "local" if account.kind == "local" else str(account.pk)
        )
        book.save(update_fields=["storage_backend"])


class Migration(migrations.Migration):

    dependencies = [
        ("audiobooks", "0003_cloudstorageaccount_audiobook_storage_account"),
    ]

    operations = [
        migrations.AddField(
            model_name="cloudstorageaccount",
            name="kind",
            field=models.CharField(
                choices=[
                    ("local", "Local server storage"),
                    ("cloudinary", "Cloudinary"),
                ],
                db_index=True,
                default="cloudinary",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="cloudstorageaccount",
            name="is_protected",
            field=models.BooleanField(
                default=False,
                help_text="System backends cannot be renamed or deleted.",
            ),
        ),
        migrations.AlterField(
            model_name="cloudstorageaccount",
            name="cloud_name",
            field=models.CharField(blank=True, max_length=80, unique=True),
        ),
        migrations.AlterField(
            model_name="cloudstorageaccount",
            name="api_key",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterField(
            model_name="cloudstorageaccount",
            name="api_secret",
            field=models.CharField(blank=True, max_length=64),
        ),
        migrations.AlterModelOptions(
            name="cloudstorageaccount",
            options={
                "ordering": ["name"],
                "verbose_name": "Storage backend",
                "verbose_name_plural": "Storage backends",
            },
        ),
        migrations.AddField(
            model_name="audiobook",
            name="storage_backend",
            field=models.CharField(
                db_index=True,
                default="local",
                editable=False,
                help_text="'local' or the ID of the Cloudinary storage account.",
                max_length=64,
            ),
        ),
        migrations.RunPython(
            _create_default_backends, migrations.RunPython.noop
        ),
        migrations.AlterField(
            model_name="audiobook",
            name="storage_account",
            field=models.ForeignKey(
                help_text="Backend this book's files live on: Local Storage "
                          "(default) or a named Cloudinary account. Locked after "
                          "upload — delete the book to move its files.",
                on_delete=models.deletion.CASCADE,
                related_name="audiobooks",
                to="audiobooks.cloudstorageaccount",
            ),
        ),
        migrations.AlterField(
            model_name="audiobook",
            name="cover",
            field=models.ImageField(
                storage=FileSystemStorage(),
                upload_to=audiobooks.models.audiobook_cover_path,
            ),
        ),
        migrations.AlterField(
            model_name="audiobook",
            name="audio_file",
            field=models.FileField(
                storage=FileSystemStorage(),
                upload_to=audiobooks.models.audiobook_file_path,
                validators=[
                    django.core.validators.FileExtensionValidator(
                        allowed_extensions=["mp3", "m4a", "aac", "ogg", "wav"]
                    )
                ],
            ),
        ),
    ]
