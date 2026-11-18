from django.urls import path

from . import views

urlpatterns = [
    path("book/<slug:slug>/", views.book_detail, name="book_detail"),
    # Must precede the generic category slug pattern.
    path("category/recent/", views.recent, name="recent"),
    path("recent/", views.recent, name="recent_short"),
    path("saved/", views.saved, name="saved"),
    path("saved/<slug:slug>/toggle/", views.toggle_saved, name="toggle_saved"),
    path("category/<slug:slug>/", views.category, name="category"),
    path("search/", views.search, name="search"),
]
