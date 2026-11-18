from django.urls import path

from . import views

urlpatterns = [
    path("play/<int:book_id>/", views.play, name="player_play"),
    path("stream/<int:book_id>/", views.stream, name="player_stream"),
    path("progress/<int:book_id>/", views.progress, name="player_progress"),
]
