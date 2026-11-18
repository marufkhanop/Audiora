import mimetypes
import re

from django.http import (
    FileResponse,
    HttpResponse,
    Http404,
    HttpResponseRedirect,
    JsonResponse,
    StreamingHttpResponse,
)
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST, require_safe

from accounts.models import PlaybackProgress
from audiobooks.models import Audiobook

LISTEN_PROMPT = "Please log in to continue listening."


def _guest_gate(request):
    """Block guests from playback routes.

    Redirects them to the login page carrying a `next` parameter (the login
    view then shows "Please log in to continue listening."). HTMX requests get
    an HX-Redirect so the whole page navigates instead of swapping the login
    HTML into the player target.
    """
    login_url = f"{reverse('accounts:login')}?next={request.get_full_path()}"
    if request.headers.get("HX-Request", "").lower() == "true":
        response = HttpResponse(status=200)
        response["HX-Redirect"] = login_url
        return response
    return HttpResponseRedirect(login_url)


def play(request, book_id):
    """HTMX endpoint: swaps the persistent bottom player for a new track."""
    if not request.user.is_authenticated:
        return _guest_gate(request)
    book = get_object_or_404(Audiobook.objects.filter(published=True), pk=book_id)
    start = 0
    if request.user.is_authenticated:
        saved = PlaybackProgress.objects.filter(
            user=request.user, book=book
        ).first()
        if saved:
            start = saved.position
    return render(
        request,
        "player/partials/player.html",
        {"book": book, "start_position": start},
    )


@require_POST
def progress(request, book_id):
    """Persist a signed-in listener's position; localStorage covers guests."""
    if not request.user.is_authenticated:
        return JsonResponse({"saved": False, "reason": "anonymous"}, status=200)
    book = get_object_or_404(Audiobook, pk=book_id, published=True)
    try:
        position = max(0, int(float(request.POST.get("position") or 0)))
        duration = max(0, int(float(request.POST.get("duration") or 0)))
    except (TypeError, ValueError):
        return JsonResponse({"saved": False, "reason": "bad-request"}, status=400)
    completed = request.POST.get("completed") in {"1", "true", "True"}
    PlaybackProgress.record(
        request.user, book, position, duration=duration, completed=completed
    )
    return JsonResponse({"saved": True})


def _file_chunks(file, remaining):
    try:
        while remaining > 0:
            chunk = file.read(min(64 * 1024, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        file.close()


@require_safe
def stream(request, book_id):
    """Serve published audio with single byte-range support for browser seeking.

    Authenticated listeners only — this is the endpoint that actually carries
    the audio bytes, so it is the security boundary for playback access.
    """
    if not request.user.is_authenticated:
        return _guest_gate(request)
    book = get_object_or_404(Audiobook, pk=book_id, published=True)
    if not book.audio_file:
        raise Http404("Audio unavailable")
    storage = book.field_storage("audio_file")
    name = book.audio_file.name
    try:
        size = storage.size(name)
    except FileNotFoundError:
        raise Http404("Audio unavailable")
    content_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    start, end = 0, size - 1
    # Range applies only to GET; HEAD describes the complete representation.
    range_header = request.headers.get("Range") if request.method == "GET" else None
    partial = False
    if range_header:
        match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
        # Ignore unsupported/malformed ranges (including multiple ranges).
        if match and any(match.groups()):
            first, last = match.groups()
            try:
                if first:
                    start = int(first)
                    end = min(int(last), size - 1) if last else size - 1
                else:
                    start = max(0, size - int(last))
                valid = size > 0 and 0 <= start <= end < size
            except ValueError:
                valid = False
            if not valid:
                response = HttpResponse(status=416)
                response["Content-Range"] = f"bytes */{size}"
                response["Accept-Ranges"] = "bytes"
                return response
            partial = True
    length = max(0, end - start + 1)
    if request.method == "HEAD":
        response = HttpResponse(content_type=content_type)
    else:
        try:
            file = storage.open(name, "rb")
        except FileNotFoundError:
            raise Http404("Audio unavailable")
        if partial:
            try:
                file.seek(start)
            except Exception:
                file.close()
                raise
            response = StreamingHttpResponse(_file_chunks(file, length), status=206, content_type=content_type)
            # Also close the file if the client disconnects before iteration starts.
            response._resource_closers.append(file.close)
        else:
            response = FileResponse(file, content_type=content_type)
    response["Accept-Ranges"] = "bytes"
    response["Content-Length"] = str(length)
    if partial:
        response["Content-Range"] = f"bytes {start}-{end}/{size}"
    return response
