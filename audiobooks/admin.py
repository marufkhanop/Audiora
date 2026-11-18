from django import forms
from django.contrib import admin
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db.models import Case, IntegerField, Value, When
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import path
from django.utils.html import format_html

from .media_cleanup import cleanup_cloudinary_orphans, cleanup_unused_media
from .models import Audiobook, Category, CloudStorageAccount
from .storage_metrics import backend_usage, dashboard_data


class StorageDestinationChoiceField(forms.ModelChoiceField):
    """Dropdown that labels the protected backend as the default."""

    def label_from_instance(self, obj):
        if obj.is_local:
            return "Local Storage (Default)"
        return f"{obj.name} — {obj.cloud_name}"


def _storage_queryset():
    """Local first, then Cloudinary accounts alphabetically."""
    return CloudStorageAccount.objects.annotate(
        _local_first=Case(
            When(kind=CloudStorageAccount.KIND_LOCAL, then=Value(0)),
            default=Value(1),
            output_field=IntegerField(),
        )
    ).order_by("_local_first", "name")


class AudiobookAdminForm(forms.ModelForm):
    storage_account = StorageDestinationChoiceField(
        queryset=CloudStorageAccount.objects.none(),
        label="Storage Destination",
        empty_label=None,
        help_text="Choose where this book's audio and cover are stored.",
    )

    class Meta:
        model = Audiobook
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        field = self.fields.get("storage_account")
        if field is None:
            return
        field.queryset = _storage_queryset()
        if not self.instance.pk:
            local = field.queryset.filter(
                kind=CloudStorageAccount.KIND_LOCAL
            ).first()
            if local:
                field.initial = local.pk


@admin.register(CloudStorageAccount)
class CloudStorageAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "cloud_name", "created_at")
    search_fields = ("name", "cloud_name")

    class Media:
        css = {"all": ("css/admin_storage.css",)}

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "storage/local/books/",
                self.admin_site.admin_view(self.local_books_view),
                name="audiobooks_cloudstorageaccount_local_books",
            ),
            path(
                "storage/<int:pk>/books/",
                self.admin_site.admin_view(self.books_view),
                name="audiobooks_cloudstorageaccount_books",
            ),
        ]
        return custom + urls

    # ---------- Dashboard ----------

    def changelist_view(self, request, extra_context=None):
        """Replace the default list with the Storage API dashboard."""
        data = dashboard_data()
        context = {
            **self.admin_site.each_context(request),
            **data,
            "title": "Storage API management",
            "opts": self.model._meta,
            "has_add_permission": self.has_add_permission(request),
        }
        return render(request, "admin/storage_accounts.html", context)

    def _render_books(self, request, account, title):
        books = (
            Audiobook.objects.filter(storage_account=account)
            .select_related("category")
            .order_by("-created_at")
        )
        usage = backend_usage(account)
        context = {
            **self.admin_site.each_context(request),
            "title": title,
            "account": account,
            "is_local": account.is_local,
            "usage": usage,
            "books": books,
            "book_count": books.count(),
            "opts": self.model._meta,
        }
        return render(request, "admin/storage_account_books.html", context)

    def local_books_view(self, request):
        return self._render_books(
            request, CloudStorageAccount.local(), "Local Server Storage"
        )

    def books_view(self, request, pk):
        account = get_object_or_404(CloudStorageAccount, pk=pk)
        return self._render_books(request, account, f"Books on “{account.name}”")

    # ---------- Add / delete ----------

    def save_model(self, request, obj, form, change):
        obj.kind = CloudStorageAccount.KIND_CLOUDINARY
        obj.is_protected = False
        obj.save()
        if not change:
            self.message_user(
                request,
                f"Storage API “{obj.name}” verified and connected.",
                messages.SUCCESS,
            )

    def delete_model(self, request, obj):
        if obj.is_local or obj.is_protected:
            raise PermissionDenied(
                "Local Server Storage is the permanent default and cannot be deleted."
            )
        books = obj.audiobooks.count()
        try:
            obj.delete()  # wipes the cloud, then cascades the DB rows
        except Exception as exc:
            self.message_user(
                request,
                f"Could not wipe files on “{obj.cloud_name}” ({exc}). "
                "The account was NOT deleted — try again when the network "
                "is back or after removing the cloud account on Cloudinary.",
                messages.ERROR,
            )
            return
        wiped = (obj.wipe_result or {}).get("deleted", 0)
        self.message_user(
            request,
            f"Storage API “{obj.name}” deleted: {wiped} file(s) wiped from "
            f"{obj.cloud_name}, {books} audiobook(s) removed from the library.",
            messages.SUCCESS,
        )

    def delete_queryset(self, request, queryset):
        for obj in queryset:
            if obj.is_local or obj.is_protected:
                continue
            self.delete_model(request, obj)

    def response_delete(self, request, obj_display, obj_id):
        # Always land back on the dashboard, not a missing change page.
        return redirect("admin:audiobooks_cloudstorageaccount_changelist")

    def response_add(self, request, obj, post_url_continue=None):
        return redirect("admin:audiobooks_cloudstorageaccount_changelist")

    def response_change(self, request, obj):
        return redirect("admin:audiobooks_cloudstorageaccount_changelist")

    def has_change_permission(self, request, obj=None):
        # No editing of credentials — delete + re-add instead (keeps the
        # panel honest: what it shows is exactly what holds the files).
        return False

    def has_delete_permission(self, request, obj=None):
        if obj is not None and (obj.is_local or obj.is_protected):
            return False
        return super().has_delete_permission(request, obj)

    def get_inline_instances(self, request, obj=None):
        return []


class CloudStorageAccountForm(forms.ModelForm):
    """Add-form with credential verification and friendly field labels."""

    class Meta:
        model = CloudStorageAccount
        fields = ["name", "cloud_name", "api_key", "api_secret"]
        widgets = {
            "api_secret": forms.PasswordInput(render_value=True),
        }
        labels = {
            "name": "Display name",
            "cloud_name": "Cloud name",
            "api_key": "API key",
            "api_secret": "API secret",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # The local row has blank credentials; Cloudinary accounts must not.
        for field_name in ("cloud_name", "api_key", "api_secret"):
            if field_name in self.fields:
                self.fields[field_name].required = True

    def clean(self):
        cleaned = super().clean()
        if self.errors:
            return cleaned
        probe = CloudStorageAccount(
            kind=CloudStorageAccount.KIND_CLOUDINARY, **cleaned
        )
        try:
            probe.verify()
        except Exception as exc:
            raise forms.ValidationError(
                f"Cloudinary rejected these credentials ({exc}). Double-check "
                "the cloud name, API key and secret, and that the machine is "
                "online."
            )
        return cleaned


# Attach the add/verify form to the admin class (fields reference the model
# above; a single clean definition point keeps the file easy to follow).
CloudStorageAccountAdmin.form = CloudStorageAccountForm


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "book_count", "created_at")
    search_fields = ("name",)
    prepopulated_fields = {"slug": ("name",)}

    def book_count(self, obj):
        return obj.audiobooks.count()

    book_count.short_description = "Total Books"


@admin.register(Audiobook)
class AudiobookAdmin(admin.ModelAdmin):
    form = AudiobookAdminForm
    list_display = (
        "cover_thumbnail",
        "title",
        "author",
        "narrator",
        "category",
        "duration_display",
        "storage_label",
        "published",
        "created_at",
    )
    list_filter = ("published", "category", "created_at", "storage_account")
    search_fields = ("title", "author", "narrator")
    list_editable = ("published",)
    readonly_fields = ("duration", "cover_preview", "created_at", "updated_at")
    prepopulated_fields = {"slug": ("title",)}
    date_hierarchy = "created_at"
    list_per_page = 25

    fieldsets = (
        ("Book Metadata", {
            "fields": ("title", "slug", "author", "narrator", "category", "description"),
        }),
        ("Media Files", {
            "fields": ("storage_account", "cover", "cover_preview", "audio_file", "duration"),
            "description": "Pick a Storage Destination for this upload. Local "
                           "Storage is the permanent default; named Cloudinary "
                           "accounts route files to that cloud. The choice is "
                           "locked after upload.",
        }),
        ("Publishing Controls", {
            "fields": ("published", "created_at", "updated_at"),
        }),
    )

    def get_readonly_fields(self, request, obj=None):
        readonly = list(super().get_readonly_fields(request, obj))
        if obj and obj.pk:
            # Storage is immutable once files exist — delete + re-upload to move.
            readonly.append("storage_account")
        return readonly

    @admin.display(description="Storage", ordering="storage_account__name")
    def storage_label(self, obj):
        if obj.storage_account is None or obj.storage_account.is_local:
            return "Local Storage (Default)"
        return obj.storage_account.name

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "media-cleanup/",
                self.admin_site.admin_view(self.media_cleanup_view),
                name="audiobooks_audiobook_media_cleanup",
            ),
        ]
        return custom + urls

    def media_cleanup_view(self, request):
        """One-click cleanup: deletes media files not referenced by any record."""
        if request.method != "POST":
            return redirect("admin:index")
        if not request.user.is_superuser:
            self.message_user(
                request, "Only superusers can run the media cleanup.",
                messages.ERROR,
            )
            return redirect("admin:index")
        s = cleanup_unused_media()
        cloud = cleanup_cloudinary_orphans()
        cloud_note = (
            f" Cloudinary: {cloud['deleted']} orphaned file(s) removed across "
            f"{cloud['accounts']} account(s)."
        )
        if cloud["failed"]:
            cloud_note += (
                f" {cloud['failed']} account(s) failed: "
                + "; ".join(cloud["errors"])
            )
        self.message_user(
            request,
            "Cleanup complete — removed "
            f"{s['audio']} unused audio file(s), {s['covers']} unused cover(s), "
            f"{s['other']} other file(s) and {s['pycache_dirs']} cache folder(s). "
            f"Freed {s['bytes_freed'] / 1024:.1f} KB.{cloud_note}",
            messages.SUCCESS if not cloud["failed"] else messages.WARNING,
        )
        return redirect("admin:index")

    @admin.display(description="Cover")
    def cover_thumbnail(self, obj):
        if obj.cover:
            return format_html(
                '<img src="{}" style="width: 45px; height: 45px; object-fit: cover; '
                'border-radius: 6px;" />',
                obj.cover_url,
            )
        return "No Cover"

    @admin.display(description="Cover Preview")
    def cover_preview(self, obj):
        if obj.cover:
            return format_html(
                '<img src="{}" style="max-width: 200px; max-height: 200px; '
                'border-radius: 8px;" />',
                obj.cover_url,
            )
        return "No Cover Uploaded"

    @admin.display(description="Duration", ordering="duration")
    def duration_display(self, obj):
        return obj.formatted_duration
