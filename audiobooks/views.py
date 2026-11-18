from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .models import Audiobook, Category, SavedBook


def published_books():
    """Every listing query joins the category — avoids N+1 lookups."""
    return Audiobook.objects.filter(published=True).select_related("category")


def book_detail(request, slug):
    book = get_object_or_404(published_books(), slug=slug)
    related = []
    if book.category:
        related = list(published_books().filter(category=book.category).exclude(pk=book.pk)[:6])
    start = 0
    is_saved = False
    if request.user.is_authenticated:
        from accounts.models import PlaybackProgress

        saved = PlaybackProgress.objects.filter(user=request.user, book=book).first()
        if saved:
            start = saved.position
        is_saved = SavedBook.objects.filter(
            user=request.user, audiobook=book
        ).exists()
    ctx = {
        "book": book,
        "related": related,
        "start_position": start,
        "is_saved": is_saved,
    }
    template = (
        "audiobooks/partials/detail_main.html"
        if request.htmx
        else "audiobooks/book_detail.html"
    )
    return render(request, template, ctx)


def category(request, slug):
    cat = get_object_or_404(Category, slug=slug)
    books = published_books().filter(category=cat)
    ctx = {
        "category": cat,
        "books": books,
        "active_category": cat.slug,
        "categories": Category.objects.all(),
    }
    template = (
        "audiobooks/partials/category_main.html"
        if request.htmx
        else "audiobooks/category.html"
    )
    return render(request, template, ctx)


def recent(request):
    """Signed-in listener's listening history, newest first."""
    from accounts.models import PlaybackProgress

    progress = (
        PlaybackProgress.recent(request.user) if request.user.is_authenticated else []
    )
    for item in progress:
        # Lets card.html draw a "resume" progress bar without extra queries.
        item.book.listen_progress = item
    ctx = {
        "progress": progress,
        "books": [item.book for item in progress],
        "active_category": "recent",
        "categories": Category.objects.all(),
        "signed_in": request.user.is_authenticated,
    }
    template = (
        "audiobooks/partials/recent_main.html"
        if request.htmx
        else "audiobooks/recent.html"
    )
    return render(request, template, ctx)


@login_required
def saved(request):
    """Books the signed-in listener bookmarked for later."""
    entries = (
        SavedBook.objects.filter(user=request.user, audiobook__published=True)
        .select_related("audiobook__category")
        .order_by("-created_at")
    )
    ctx = {
        "entries": entries,
        "books": [entry.audiobook for entry in entries],
        "active_category": "saved",
        "categories": Category.objects.all(),
        "signed_in": True,
    }
    template = (
        "audiobooks/partials/saved_main.html"
        if request.htmx
        else "audiobooks/saved.html"
    )
    return render(request, template, ctx)


@login_required
@require_POST
def toggle_saved(request, slug):
    """Save or unsave a book, then return the listener where they were."""
    book = get_object_or_404(published_books(), slug=slug)
    entry = SavedBook.objects.filter(user=request.user, audiobook=book).first()
    if entry:
        entry.delete()
    else:
        SavedBook.objects.create(user=request.user, audiobook=book)

    next_url = request.POST.get("next") or ""
    if not url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        next_url = reverse("book_detail", args=[book.slug])
    return redirect(next_url)


def search(request):
    q = (request.GET.get("q") or "").strip()
    books = published_books()
    if q:
        books = books.filter(
            Q(title__icontains=q) | Q(author__icontains=q) | Q(narrator__icontains=q)
        )[:24]
    ctx = {"books": books, "q": q, "categories": Category.objects.all()}

    if request.htmx and request.headers.get("HX-Target") in {
        "search-panel", "search-drawer-results",
    }:
        return render(request, "audiobooks/partials/search_results.html", ctx)

    template = (
        "audiobooks/partials/search_page.html"
        if request.htmx
        else "audiobooks/search.html"
    )
    return render(request, template, ctx)
