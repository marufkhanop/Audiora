"""
Keeps storage backends free of dead files.

Django never deletes files when a model instance is deleted, so without these
hooks every deleted/replaced audiobook would leave its audio and cover behind
on local disk or on the Cloudinary account that held it.

* pre_delete  → delete the record's audio + cover from its backend
* pre_save    → remember which files the record previously pointed at
* post_save   → if a field now points to a NEW file, delete the old copy

`deleteorphanedmedia` / `cleanup_media` remain the final safety nets for files
missed here (e.g. a failed save after upload).
"""
import logging

from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

from .models import Audiobook, CloudStorageAccount

logger = logging.getLogger(__name__)

MEDIA_FIELDS = ("cover", "audio_file")


@receiver(pre_delete, sender=CloudStorageAccount)
def protect_local_backend(sender, instance, **kwargs):
    """Stop queryset-level deletes from removing the system backend."""
    if instance.is_local or instance.is_protected:
        from django.db.models.deletion import ProtectedError

        raise ProtectedError(
            "Local Server Storage is the permanent default backend and cannot "
            "be deleted.",
            set(),
        )


def _still_referenced(field_name, name, exclude_pk=None):
    """True when another audiobook row still points at this stored file."""
    qs = Audiobook.objects.filter(**{field_name: name})
    if exclude_pk is not None:
        qs = qs.exclude(pk=exclude_pk)
    return qs.exists()


def _delete_stored_file(field_file, storage):
    """Delete a file from its backend, tolerating network/filesystem failures."""
    try:
        storage.delete(field_file.name)
    except Exception:  # noqa: BLE001 — never block DB deletion on storage errors
        logger.warning(
            "Could not delete %s from its storage backend (it will be cleaned "
            "up by the media cleanup command).", field_file.name, exc_info=True,
        )


@receiver(pre_delete, sender=Audiobook)
def audiobook_delete_files(sender, instance, **kwargs):
    for name in MEDIA_FIELDS:
        field_file = getattr(instance, name, None)
        if field_file and field_file.name and not _still_referenced(
            name, field_file.name, exclude_pk=instance.pk
        ):
            _delete_stored_file(field_file, instance.field_storage(name))


@receiver(pre_save, sender=Audiobook)
def audiobook_stash_old_files(sender, instance, **kwargs):
    instance._old_media = {}
    if instance.pk:
        old = Audiobook.objects.filter(pk=instance.pk).values_list(*MEDIA_FIELDS).first()
        if old:
            instance._old_media = dict(zip(MEDIA_FIELDS, old))


@receiver(post_save, sender=Audiobook)
def audiobook_delete_replaced_files(sender, instance, created, **kwargs):
    if created:
        return
    old_media = getattr(instance, "_old_media", {})
    for name in MEDIA_FIELDS:
        old_name = old_media.get(name)
        current = getattr(instance, name, None)
        if old_name and current and current.name and current.name != old_name:
            if _still_referenced(name, old_name, exclude_pk=instance.pk):
                continue
            try:
                instance.field_storage(name).delete(old_name)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Could not delete replaced file %s from Cloudinary.",
                    old_name, exc_info=True,
                )
