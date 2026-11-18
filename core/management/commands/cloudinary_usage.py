"""Show Cloudinary plan quota usage (storage / bandwidth / credits)."""
from django.core.management.base import BaseCommand

from core.templatetags.cloudinary_admin import get_cloudinary_usage


class Command(BaseCommand):
    help = "Show how much of the Cloudinary plan (storage, bandwidth, credits) is used."

    def add_arguments(self, parser):
        parser.add_argument(
            "--refresh",
            action="store_true",
            help="Bypass the 10-minute cache and query Cloudinary right now.",
        )

    def handle(self, *args, **options):
        u = get_cloudinary_usage(force=options["refresh"])

        if u["status"] == "unconfigured":
            self.stdout.write(self.style.WARNING(
                "Cloudinary is not configured — media is stored on local disk."))
            return
        if u["status"] == "error":
            self.stdout.write(self.style.ERROR(
                f"Could not reach Cloudinary: {u['error']}"))
            return

        bar_len = 30
        filled = int(round(u["credits_pct"] / 100 * bar_len))
        bar = "|" + "#" * filled + "." * (bar_len - filled) + "|"

        self.stdout.write(f"Cloudinary {u['plan']} plan — used {u['credits_pct']}% of monthly credits")
        self.stdout.write(bar)
        self.stdout.write(f"  credits        : {u['credits_used']:.2f} / {u['credits_limit']:.0f} GB")
        self.stdout.write(f"  media stored   : {u['storage_used']}")
        self.stdout.write(f"  bandwidth (mo) : {u['bandwidth_used']}")
        self.stdout.write(f"  assets         : {u['objects']}")
        self.stdout.write(f"  transformations: {u['transformations']}")
        self.stdout.write(f"  (sampled {u['fetched_at']}, cached 10 min — use --refresh for live data)")
