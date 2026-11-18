"""Listener authentication: email/password plus Google (Gmail) sign-in.

Google OAuth uses the standard authorization-code flow implemented with the
stdlib ``urllib`` — no extra dependency. Configure GOOGLE_CLIENT_ID /
GOOGLE_CLIENT_SECRET in .env to enable it; without them the button explains
that Google sign-in is not configured yet.
"""
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods, require_POST

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _safe_next(request, fallback="/"):
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def _google_enabled():
    return bool(
        getattr(settings, "GOOGLE_CLIENT_ID", "")
        and getattr(settings, "GOOGLE_CLIENT_SECRET", "")
    )


def _build_user(email, name=""):
    """Return an existing user for ``email`` or create a listener account."""
    User = get_user_model()
    email = email.strip().lower()
    user = User.objects.filter(email__iexact=email).first()
    if user:
        return user, False
    username = email
    suffix = 0
    while User.objects.filter(username=username).exists():
        suffix += 1
        username = f"{email}-{suffix}"
    first, _, last = (name or "").partition(" ")
    user = User.objects.create_user(
        username=username, email=email, password=None
    )
    # Google accounts have no local password until they set one.
    user.set_unusable_password()
    user.first_name = first[:150]
    user.last_name = last[:150]
    user.save(update_fields=["first_name", "last_name"])
    return user, True


@require_http_methods(["GET", "POST"])
def signup(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        email = (request.POST.get("email") or "").strip().lower()
        password = request.POST.get("password") or ""
        confirm = request.POST.get("confirm") or ""
        User = get_user_model()
        errors = []
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            errors.append("Enter a valid email address.")
        if len(password) < 8:
            errors.append("Use at least 8 characters for your password.")
        if password != confirm:
            errors.append("The two passwords do not match.")
        if not errors and User.objects.filter(email__iexact=email).exists():
            errors.append("An account with that email already exists. Log in instead.")

        if not errors:
            user, _ = _build_user(email)
            user.set_password(password)
            user.save(update_fields=["password"])
            login(request, user)
            messages.success(request, "Welcome to Audiora. Your library syncs from now on.")
            return redirect(_safe_next(request))

        return render(
            request,
            "accounts/signup.html",
            {
                "errors": errors,
                "email": email,
                "google_enabled": _google_enabled(),
            },
            status=400,
        )

    return render(
        request,
        "accounts/signup.html",
        {"google_enabled": _google_enabled(), "next": _safe_next(request)},
    )


@require_http_methods(["GET", "POST"])
def login_view(request):
    if request.user.is_authenticated:
        return redirect("home")

    if request.method == "POST":
        identifier = (request.POST.get("email") or "").strip()
        password = request.POST.get("password") or ""
        User = get_user_model()
        username = identifier
        if "@" in identifier:
            owner = User.objects.filter(email__iexact=identifier).first()
            if owner:
                username = owner.get_username()
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect(_safe_next(request))

        return render(
            request,
            "accounts/login.html",
            {
                "errors": ["That email and password combination did not match."],
                "email": identifier,
                "google_enabled": _google_enabled(),
            },
            status=400,
        )

    next_target = request.GET.get("next")
    if next_target and next_target != "/":
        messages.info(request, "Please log in to continue listening.")
    return render(
        request,
        "accounts/login.html",
        {"google_enabled": _google_enabled(), "next": _safe_next(request)},
    )


@require_POST
def logout_view(request):
    logout(request)
    return redirect("home")


def google_start(request):
    """Redirect to Google's consent screen."""
    if not _google_enabled():
        messages.error(
            request,
            "Google sign-in is not configured. Set GOOGLE_CLIENT_ID and "
            "GOOGLE_CLIENT_SECRET in .env, or sign up with your email instead.",
        )
        return redirect("accounts:signup")

    state = secrets.token_urlsafe(24)
    request.session["google_state"] = state
    request.session["google_next"] = _safe_next(request)
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")
    if not redirect_uri:
        redirect_uri = request.build_absolute_uri(reverse("accounts:google_callback"))
    params = {
        "client_id": settings.GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return redirect(f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}")


def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode())


def google_callback(request):
    state = request.GET.get("state", "")
    expected = request.session.pop("google_state", "")
    next_url = request.session.pop("google_next", "/") or "/"
    if not expected or not secrets.compare_digest(state, expected):
        messages.error(request, "That Google sign-in link expired. Please try again.")
        return redirect("accounts:login")

    if request.GET.get("error"):
        messages.error(request, "Google sign-in was cancelled.")
        return redirect("accounts:login")

    code = request.GET.get("code", "")
    redirect_uri = getattr(settings, "GOOGLE_REDIRECT_URI", "")
    if not redirect_uri:
        redirect_uri = request.build_absolute_uri(reverse("accounts:google_callback"))

    try:
        token = _post_form(
            GOOGLE_TOKEN_URL,
            {
                "code": code,
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        info_request = urllib.request.Request(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        with urllib.request.urlopen(info_request, timeout=10) as response:
            profile = json.loads(response.read().decode())
    except (urllib.error.URLError, KeyError, ValueError):
        messages.error(request, "Google could not verify that account. Try again.")
        return redirect("accounts:login")

    email = profile.get("email", "")
    if not email or not profile.get("email_verified", True):
        messages.error(request, "Your Google account has no verified email address.")
        return redirect("accounts:login")

    user, created = _build_user(email, profile.get("name", ""))
    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    if created:
        messages.success(request, "Welcome to Audiora. Your library syncs from now on.")
    return redirect(
        next_url if url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={request.get_host()},
            require_https=request.is_secure(),
        ) else "/"
    )
