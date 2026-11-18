"""Metrics and dashboard data for the hybrid storage backends.

Local storage is measured directly from MEDIA_ROOT. Cloudinary usage comes
from the Admin API and is cached so the dashboard never hammers their
rate-limited endpoints. Everything returned is presentation-ready *and*
keeps raw byte counts so the overview bar can combine all backends.
"""
import os

from django.conf import settings
from django.core.cache import cache

from .models import Audiobook, CloudStorageAccount

USAGE_TTL = 10 * 60
ERROR_TTL = 60

# Distinct lavender/purple-family colours for the combined stacked bar.
SEGMENT_COLORS = ["#8B5CF6", "#6366F1", "#D946EF", "#A855F7", "#22D3EE", "#F59E0B"]


def humanize_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def local_storage_stats():
    """Count and total size of files actually present under MEDIA_ROOT."""
    root = str(settings.MEDIA_ROOT)
    count = 0
    size = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in filenames:
            if name.startswith("."):
                continue
            path = os.path.join(dirpath, name)
            try:
                size += os.path.getsize(path)
                count += 1
            except OSError:
                continue
    return {"files": count, "bytes": size}


def _usage_cache_key(account):
    return f"storage-backend-usage-{account.pk}-{account.cloud_name}"


def backend_usage(account):
    """Uniform usage dict for local disk or any Cloudinary account."""
    if account.is_local:
        stats = local_storage_stats()
        return {
            "status": "ok",
            "local": True,
            "storage_bytes": stats["bytes"],
            "bandwidth_bytes": 0,
            "objects": stats["files"],
            "credits_used": 0.0,
            "credits_limit": 0.0,
            "credits_pct": 0.0,
            "storage_used": humanize_bytes(stats["bytes"]),
            "bandwidth_used": "—",
            "credits_label": "Unlimited · no API quota",
            "error": "",
        }

    key = _usage_cache_key(account)
    cached = cache.get(key)
    if cached is not None:
        return cached

    try:
        raw = account.usage()
    except Exception as exc:  # offline / revoked credentials
        result = {
            "status": "error",
            "local": False,
            "storage_bytes": 0,
            "bandwidth_bytes": 0,
            "objects": 0,
            "credits_used": 0.0,
            "credits_limit": 0.0,
            "credits_pct": 0.0,
            "storage_used": "—",
            "bandwidth_used": "—",
            "credits_label": "Unavailable",
            "error": str(exc) or "Cloudinary is unreachable.",
        }
        cache.set(key, result, ERROR_TTL)
        return result

    credits = raw.get("credits", {}) or {}
    used = float(credits.get("usage", 0) or 0)
    limit = float(credits.get("limit", 0) or 0)
    storage_bytes = int((raw.get("storage", {}) or {}).get("usage", 0) or 0)
    bandwidth_bytes = int((raw.get("bandwidth", {}) or {}).get("usage", 0) or 0)
    objects = int((raw.get("objects", {}) or {}).get("usage", 0) or 0)
    result = {
        "status": "ok",
        "local": False,
        "storage_bytes": storage_bytes,
        "bandwidth_bytes": bandwidth_bytes,
        "objects": objects,
        "credits_used": used,
        "credits_limit": limit,
        "credits_pct": round(used / limit * 100, 1) if limit else 0.0,
        "storage_used": humanize_bytes(storage_bytes),
        "bandwidth_used": humanize_bytes(bandwidth_bytes),
        "credits_label": f"{used:.2f} / {limit:.0f} GB credits",
        "error": "",
    }
    cache.set(key, result, USAGE_TTL)
    return result


def clear_usage_cache(account=None):
    """Drop cached usage after a backend changes (add/delete/upload)."""
    if account is None:
        for acct in CloudStorageAccount.objects.all():
            cache.delete(_usage_cache_key(acct))
        return
    cache.delete(_usage_cache_key(account))


def backend_item(account, color_index=0):
    """One dashboard/detail card worth of data for a backend."""
    usage = backend_usage(account)
    books = Audiobook.objects.filter(storage_account=account)
    item = {
        "obj": account,
        "usage": usage,
        "books": books.count(),
        "storage_bytes": usage["storage_bytes"],
        "bandwidth_bytes": usage["bandwidth_bytes"],
        "objects": usage["objects"],
        "is_local": account.is_local,
        "color": SEGMENT_COLORS[color_index % len(SEGMENT_COLORS)],
        "share_pct": 0.0,
    }
    return item


def dashboard_data():
    """Everything the Storage API dashboard needs, including combined totals."""
    local = CloudStorageAccount.local()
    clouds = list(
        CloudStorageAccount.objects.exclude(kind=CloudStorageAccount.KIND_LOCAL)
        .order_by("name")
    )
    items = [backend_item(local, 0)]
    items += [backend_item(acct, i + 1) for i, acct in enumerate(clouds)]

    total_storage = sum(item["storage_bytes"] for item in items)
    total_bandwidth = sum(item["bandwidth_bytes"] for item in items)
    total_objects = sum(item["objects"] for item in items)
    for item in items:
        item["share_pct"] = (
            round(item["storage_bytes"] / total_storage * 100, 2)
            if total_storage
            else 0.0
        )

    return {
        "local": local,
        "local_item": items[0],
        "cloud_items": items[1:],
        "items": items,
        "total_books": Audiobook.objects.count(),
        "total_storage_bytes": total_storage,
        "total_storage": humanize_bytes(total_storage),
        "total_bandwidth_bytes": total_bandwidth,
        "total_bandwidth": humanize_bytes(total_bandwidth),
        "total_objects": total_objects,
        "backend_count": len(items),
        "cloud_count": len(clouds),
        "active_backends": sum(
            1
            for item in items
            if item["is_local"] or item["usage"]["status"] == "ok"
        ),
    }
