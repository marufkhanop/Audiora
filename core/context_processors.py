def navigation(request):
    """Sidebar / bottom-nav links plus the listener's resume point.

    Available on every page, including HTMX partial renders.
    """
    from django.conf import settings

    from accounts.models import PlaybackProgress
    from audiobooks.models import Category

    user = getattr(request, "user", None)
    recent = []
    resume = None
    if user is not None and user.is_authenticated:
        recent = PlaybackProgress.recent(user, limit=12)
        resume = recent[0] if recent else None

    return {
        "nav_categories": Category.objects.all(),
        "recent_progress": recent,
        "resume": resume,
        # Flat lookups so player.html never has to resolve `resume.book`
        # (which raises when resume is None).
        "resume_book": resume.book if resume else None,
        "resume_position": resume.position if resume else 0,
        "google_enabled": bool(
            settings.GOOGLE_CLIENT_ID and settings.GOOGLE_CLIENT_SECRET
        ),
    }
