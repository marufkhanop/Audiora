"""
Live Cloudinary usage (storage / bandwidth / credits) for the Django admin
dashboard and the `cloudinary_usage` management command.

The Admin API result is cached for 10 minutes so that loading the admin index
does not hammer Cloudinary's rate-limited Admin API; error states are cached
for only 1 minute so it retries quickly once the network is back.
"""
import datetime

import cloudinary
import cloudinary.api
from django import template
from django.conf import settings
from django.core.cache import cache

register = template.Library()

CACHE_KEY = "cloudinary_admin_usage"
OK_TTL = 10 * 60      # seconds a successful result stays cached
ERROR_TTL = 60        # seconds before retrying after a failure


def _humanize_bytes(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} TB"


def get_cloudinary_usage(force=False):
    """Return a dict describing the state of the Cloudinary account quota."""
    if not getattr(settings, "CLOUDINARY_CONFIGURED", False):
        return {"status": "unconfigured"}

    if not force:
        cached = cache.get(CACHE_KEY)
        if cached is not None:
            return cached

    try:
        usage = cloudinary.api.usage()
    except Exception as exc:
        result = {
            "status": "error",
            "error": str(exc) or "Cloudinary is unreachable (offline or API error).",
        }
        cache.set(CACHE_KEY, result, ERROR_TTL)
        return result

    storage = usage.get("storage", {})
    bandwidth = usage.get("bandwidth", {})
    credits = usage.get("credits", {})
    objects = usage.get("objects", {})
    transformations = usage.get("transformations", {})

    used = float(credits.get("usage", 0) or 0)
    limit = float(credits.get("limit", 0) or 0)
    pct = (used / limit * 100) if limit else 0

    result = {
        "status": "ok",
        "plan": usage.get("plan", "Unknown"),
        "credits_used": used,
        "credits_limit": limit,
        "credits_pct": round(pct, 1),
        "storage_used": _humanize_bytes(storage.get("usage", 0)),
        "bandwidth_used": _humanize_bytes(bandwidth.get("usage", 0)),
        "objects": objects.get("usage", 0),
        "transformations": transformations.get("usage", 0),
        "fetched_at": datetime.datetime.now().strftime("%H:%M"),
    }
    cache.set(CACHE_KEY, result, OK_TTL)
    return result


@register.simple_tag
def cloudinary_usage(force=False):
    return get_cloudinary_usage(force=force)
