import os

from django import template
from django.contrib.staticfiles import finders
from django.templatetags.static import static

register = template.Library()


@register.simple_tag
def static_v(path):
    """Static file URL with an mtime-based version: style.css?v=169…

    Phones cache CSS/JS aggressively; without a version query a deployed
    edit can keep serving the stale file (e.g. a "dead" close button after
    the drawer markup/JS changed). The mtime changes on every edit, so the
    URL changes and the cache is bypassed automatically.
    """
    url = static(path)
    real = finders.find(path)
    if real:
        try:
            url = f"{url}?v={int(os.path.getmtime(real))}"
        except OSError:
            pass
    return url
