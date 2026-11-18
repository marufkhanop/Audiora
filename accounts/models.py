from django.conf import settings
from django.db import models


class PlaybackProgress(models.Model):
    """Server-side listening position for a signed-in listener.

    Mirrors the browser's localStorage keys so a listener can resume on any
    device, and doubles as the "recently listened" history.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="playback_progress",
    )
    book = models.ForeignKey(
        "audiobooks.Audiobook",
        on_delete=models.CASCADE,
        related_name="progress",
    )
    position = models.PositiveIntegerField(default=0)
    completed = models.BooleanField(default=False)
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "book"], name="unique_progress_per_user_book"
            )
        ]
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user} · {self.book} @ {self.position}s"

    @property
    def percent(self):
        duration = self.book.duration
        if not duration:
            return 0
        return min(100, round(self.position / duration * 100))

    @property
    def position_hms(self):
        hours, rem = divmod(self.position, 3600)
        minutes, seconds = divmod(rem, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @property
    def resume_label(self):
        if self.completed:
            return "Play again"
        return f"Resume {self.position_hms}"

    @classmethod
    def record(cls, user, book, position, duration=0, completed=False):
        """Create or update a position, but never move a listener backwards
        to zero because a page reloaded before metadata loaded."""
        duration = duration or book.duration
        position = max(0, int(position))
        if duration and position >= duration - 2:
            completed = True
        progress, created = cls.objects.get_or_create(
            user=user, book=book, defaults={"position": position}
        )
        progress.position = position
        progress.completed = completed
        progress.save(update_fields=["position", "completed", "updated_at"])
        return progress

    @classmethod
    def recent(cls, user, limit=12):
        """Most recently touched books, newest first, published only."""
        if not user or not user.is_authenticated:
            return []
        return list(
            cls.objects.filter(user=user, book__published=True)
            .select_related("book", "book__category")
            .order_by("-updated_at")[:limit]
        )

    @classmethod
    def latest(cls, user):
        """The book to preload into the persistent player, if any."""
        items = cls.recent(user, limit=1)
        return items[0] if items else None
