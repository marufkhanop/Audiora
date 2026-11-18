"""Session isolation for the Django admin and the public site.

Django normally uses one session cookie for the whole project. That means an
admin login overwrites the listener's session (and logging out on the public
site would log the admin out). This middleware routes requests under
``/admin/`` through a dedicated cookie while leaving the public session
completely untouched.
"""
from django.conf import settings
from django.contrib.sessions.middleware import SessionMiddleware


class SplitSessionMiddleware(SessionMiddleware):
    def _is_admin_request(self, request):
        prefix = getattr(settings, "ADMIN_SESSION_PATH_PREFIX", "/admin/")
        return request.path.startswith(prefix)

    def process_request(self, request):
        request._split_admin_session = self._is_admin_request(request)
        if request._split_admin_session:
            admin_cookie = getattr(
                settings, "ADMIN_SESSION_COOKIE_NAME", "audiora_admin_sessionid"
            )
            public_cookie = settings.SESSION_COOKIE_NAME
            # Let the standard session machinery see only the admin cookie.
            if admin_cookie in request.COOKIES:
                request.COOKIES[public_cookie] = request.COOKIES[admin_cookie]
            else:
                request.COOKIES.pop(public_cookie, None)
        return super().process_request(request)

    def process_response(self, request, response):
        response = super().process_response(request, response)
        if getattr(request, "_split_admin_session", False):
            public_cookie = settings.SESSION_COOKIE_NAME
            admin_cookie = getattr(
                settings, "ADMIN_SESSION_COOKIE_NAME", "audiora_admin_sessionid"
            )
            if public_cookie in response.cookies:
                morsel = response.cookies.pop(public_cookie)
                response.set_cookie(
                    admin_cookie,
                    morsel.value,
                    max_age=int(morsel["max-age"]) if morsel["max-age"] else None,
                    expires=morsel["expires"] or None,
                    path=morsel["path"] or "/",
                    domain=morsel["domain"] or None,
                    secure=bool(morsel["secure"]),
                    httponly=bool(morsel["httponly"]),
                    samesite=morsel["samesite"] or None,
                )
        return response
