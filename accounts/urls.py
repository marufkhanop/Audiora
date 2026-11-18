from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("signup/", views.signup, name="signup"),
    path("logout/", views.logout_view, name="logout"),
    path("google/", views.google_start, name="google_start"),
    path("google/callback/", views.google_callback, name="google_callback"),
]
