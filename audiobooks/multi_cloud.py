"""
Per-account Cloudinary storage bindings for the hybrid storage system.

Local Server Storage is the default and lives outside this module. These
bindings let a `CloudStorageAccount` DB row act as its own Cloudinary backend:
every call (upload, delete, URL, admin API) receives explicit per-call
credentials, so no global config is mutated and concurrent uploads to different
accounts cannot interfere.
"""
import logging
import os

import cloudinary
import cloudinary.api
import cloudinary.uploader
import cloudinary.utils
from django.core.exceptions import PermissionDenied  # noqa: F401 (re-exported for admin use)

from cloudinary_storage.storage import MediaCloudinaryStorage, VideoMediaCloudinaryStorage

logger = logging.getLogger(__name__)


class AccountBoundMixin:
    """Cloudinary storage bound to one CloudStorageAccount row."""

    account = None

    def __init__(self, account):
        self.account = account
        super().__init__()

    def _creds(self):
        return {
            "cloud_name": self.account.cloud_name,
            "api_key": self.account.api_key,
            "api_secret": self.account.api_secret,
        }

    def _upload(self, name, content):
        options = self._creds()
        options.update({
            "use_filename": True,
            "resource_type": self._get_resource_type(name),
            "tags": self.TAG,
        })
        folder = os.path.dirname(name)
        if folder:
            options["folder"] = folder
        return cloudinary.uploader.upload(content, **options)

    def delete(self, name):
        response = cloudinary.uploader.destroy(
            name, invalidate=True, resource_type=self._get_resource_type(name),
            **self._creds()
        )
        return response.get("result") == "ok"

    def _get_url(self, name):
        name = self._prepend_prefix(name)
        resource = cloudinary.CloudinaryResource(
            name, default_resource_type=self._get_resource_type(name)
        )
        return resource.build_url(cloud_name=self.account.cloud_name, secure=True)


class AccountBoundMediaStorage(AccountBoundMixin, MediaCloudinaryStorage):
    """Covers (images) on a specific Cloudinary account."""


class AccountBoundVideoStorage(AccountBoundMixin, VideoMediaCloudinaryStorage):
    """Audio files on a specific Cloudinary account (video resource type)."""


STORAGE_CLASSES = {
    "cover": AccountBoundMediaStorage,
    "audio_file": AccountBoundVideoStorage,
}


def ping_account(account):
    """Verify credentials by calling the Admin API. Raises on failure."""
    return cloudinary.api.ping(**{
        "cloud_name": account.cloud_name,
        "api_key": account.api_key,
        "api_secret": account.api_secret,
    })


def account_usage(account):
    """Return the Admin API usage dict for one account."""
    return cloudinary.api.usage(**{
        "cloud_name": account.cloud_name,
        "api_key": account.api_key,
        "api_secret": account.api_secret,
    })


def wipe_account_media(account):
    """Delete every 'media'-tagged upload (covers + audio) on this account.

    Returns {"deleted": <count>, "already_gone": bool}.
    Raises cloudinary errors on network/auth failures that are NOT
    "account gone" — callers decide whether to abort.
    """
    from cloudinary_storage import app_settings

    tag = app_settings.MEDIA_TAG
    creds = {
        "cloud_name": account.cloud_name,
        "api_key": account.api_key,
        "api_secret": account.api_secret,
    }
    deleted = 0
    already_gone = False
    for resource_type in ("image", "video"):
        # Delete in rounds until the tag is empty (each call handles a batch).
        for _ in range(100):  # hard safety cap
            found = cloudinary.api.resources_by_tag(
                tag, resource_type=resource_type, max_results=1, **creds
            )
            if not found.get("resources"):
                break
            result = cloudinary.api.delete_resources_by_tag(
                tag, resource_type=resource_type, **creds
            )
            deleted += sum(1 for v in result.get("deleted", {}).values() if v == "deleted")
    return {"deleted": deleted, "already_gone": already_gone}


def list_account_media(account):
    """Return [(public_id, resource_type)] for all 'media'-tagged uploads."""
    from cloudinary_storage import app_settings

    tag = app_settings.MEDIA_TAG
    creds = {
        "cloud_name": account.cloud_name,
        "api_key": account.api_key,
        "api_secret": account.api_secret,
    }
    found = []
    for resource_type in ("image", "video"):
        cursor = None
        for _ in range(100):  # pagination safety cap
            kwargs = {
                "resource_type": resource_type,
                "max_results": 500,
                **creds,
            }
            if cursor:
                kwargs["next_cursor"] = cursor
            page = cloudinary.api.resources_by_tag(tag, **kwargs)
            found.extend(
                (resource["public_id"], resource_type)
                for resource in page.get("resources", [])
            )
            cursor = page.get("next_cursor")
            if not cursor:
                break
    return found


def delete_account_resources(account, public_ids, resource_type):
    """Delete a batch of resources from one account; return deleted count."""
    if not public_ids:
        return 0
    result = cloudinary.api.delete_resources(
        list(public_ids),
        resource_type=resource_type,
        type="upload",
        invalidate=True,
        **{
            "cloud_name": account.cloud_name,
            "api_key": account.api_key,
            "api_secret": account.api_secret,
        },
    )
    return sum(1 for value in result.get("deleted", {}).values() if value == "deleted")
