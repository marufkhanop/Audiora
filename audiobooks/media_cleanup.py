"""Orphaned media cleanup — deletes files no longer referenced by the database."""
import glob
import os
import shutil

from django.conf import settings

from .models import Audiobook

MEDIA_DIRS = ("audio", "covers")


def find_unused_media():
    """Return (unused_paths, freed_bytes) for local media files not linked to any record."""
    referenced = set()
    for book in Audiobook.objects.select_related("storage_account"):
        account = book.storage_account
        if account is not None and not account.is_local:
            continue  # cloud-backed file names never live under MEDIA_ROOT
        referenced.update(
            name for name in (book.audio_file.name, book.cover.name) if name
        )

    unused = []
    for folder in MEDIA_DIRS:
        pattern = os.path.join(settings.MEDIA_ROOT, folder, "*")
        for path in glob.glob(pattern):
            if not os.path.isfile(path):
                continue
            rel = os.path.relpath(path, settings.MEDIA_ROOT).replace(os.sep, "/")
            if rel not in referenced:
                unused.append(path)

    probe = os.path.join(settings.MEDIA_ROOT, ".flock_probe")
    if os.path.isfile(probe):
        unused.append(probe)

    return unused, sum(os.path.getsize(p) for p in unused if os.path.exists(p))


def cleanup_unused_media():
    """Delete unused media files, stray bytecode caches and lock probes.

    Returns a summary dict; never touches the database.
    """
    unused, freed = find_unused_media()

    summary = {"audio": 0, "covers": 0, "other": 0}
    for path in unused:
        folder = os.path.basename(os.path.dirname(path))
        summary[folder if folder in summary else "other"] += 1
        try:
            os.remove(path)
        except OSError:
            pass

    removed_cache = 0
    for cache_dir in glob.glob(os.path.join(settings.BASE_DIR, "**", "__pycache__"), recursive=True):
        if os.path.isdir(cache_dir):
            shutil.rmtree(cache_dir, ignore_errors=True)
            removed_cache += 1

    summary["pycache_dirs"] = removed_cache
    summary["bytes_freed"] = freed
    return summary


def cleanup_cloudinary_orphans():
    """Purge untagged-by-DB media from every active Cloudinary account.

    Returns {"accounts", "deleted", "failed", "errors"}. Network/auth errors
    are collected instead of aborting the whole cleanup.
    """
    from .models import CloudStorageAccount

    summary = {"accounts": 0, "deleted": 0, "failed": 0, "errors": []}
    accounts = CloudStorageAccount.objects.exclude(
        kind=CloudStorageAccount.KIND_LOCAL
    )
    for account in accounts:
        summary["accounts"] += 1
        try:
            from .multi_cloud import delete_account_resources, list_account_media

            referenced = set()
            for audio_name, cover_name in Audiobook.objects.filter(
                storage_account=account
            ).values_list("audio_file", "cover"):
                referenced.update(name for name in (audio_name, cover_name) if name)
            resources = list_account_media(account)
            orphan_ids = {"image": [], "video": []}
            for public_id, resource_type in resources:
                if public_id not in referenced:
                    orphan_ids[resource_type].append(public_id)
            for resource_type, ids in orphan_ids.items():
                for start in range(0, len(ids), 100):
                    summary["deleted"] += delete_account_resources(
                        account, ids[start:start + 100], resource_type
                    )
        except Exception as exc:  # noqa: BLE001 — one bad account must not block others
            summary["failed"] += 1
            summary["errors"].append(f"{account.name}: {exc}")
    return summary
