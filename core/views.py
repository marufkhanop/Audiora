from django.db.models import Count, Prefetch, Q
from django.shortcuts import render

from audiobooks.models import Audiobook, Category


def home(request):
    # Guests: a streaming-style page — the latest upload as a big hero, a
    # search bar, scrollable category filter chips, and the library laid out
    # as one horizontal, right-to-left scrollable row per category.
    if not request.user.is_authenticated:
        q = (request.GET.get("q") or "").strip()
        cat_slug = (request.GET.get("category") or "").strip()

        featured = (
            Audiobook.objects.filter(published=True)
            .select_related("category")
            .order_by("-created_at")
            .first()
        )
        chips = (
            Category.objects.annotate(
                book_count=Count("audiobooks", filter=Q(audiobooks__published=True))
            )
            .filter(book_count__gt=0)
            .order_by("name")
        )

        results = None
        rows = []  # (category, [books]) pairs for the horizontal rows
        active_category = None
        if q:
            results = (
                Audiobook.objects.filter(published=True)
                .filter(
                    Q(title__icontains=q)
                    | Q(author__icontains=q)
                    | Q(narrator__icontains=q)
                )
                .select_related("category")
                .order_by("-created_at")[:40]
            )
        else:
            cats = chips
            if cat_slug:
                cats = chips.filter(slug=cat_slug)
                active_category = cats.first()
                if active_category is None:  # unknown slug → show everything
                    cats = chips
            per_cat = Prefetch(
                "audiobooks",
                queryset=Audiobook.objects.filter(published=True)
                .select_related("category")
                .order_by("-created_at"),
            )
            for cat in cats.prefetch_related(per_cat):
                books = list(cat.audiobooks.all())
                if books:
                    rows.append((cat, books))

        ctx = {
            "featured": featured,
            "chips": chips,
            "rows": rows,
            "results": results,
            "q": q,
            "active_category": active_category,
        }
        template = "core/guest_home.html"
        if request.htmx:
            # Only the library block is swapped when searching/filtering;
            # navigation into the home page must render the whole page body
            # (hero + search + library) inside #main-content.
            target = request.headers.get("HX-Target", "")
            template = (
                "audiobooks/partials/guest_library.html"
                if target == "guest-library"
                else "core/guest_home_main.html"
            )
        return render(request, template, ctx)
    books = Audiobook.objects.filter(published=True).select_related("category")
    ctx = {
        "featured": books.first(),
        "books": books,
        "active_category": None,
        "categories": Category.objects.all(),
    }
    template = (
        "audiobooks/partials/home_main.html" if request.htmx else "core/home.html"
    )
    return render(request, template, ctx)
