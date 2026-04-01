from django.urls import path, re_path

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    path("legacy/", views.legacy_index, name="legacy_index"),
    path("app/", views.modern_app, name="modern_app"),
    re_path(r"^app/.*$", views.modern_app, name="modern_app_catchall"),
    re_path(r"^(targets|explorer|operations|unfollow|accounts|settings)/?$", views.modern_shortcut, name="modern_shortcut"),
    path("healthz/", views.healthz, name="healthz"),
    path("secret-drop/", views.secret_drop, name="secret_drop"),
    re_path(r"^api/(?P<path>.*)$", views.api_proxy, name="api_proxy"),
]
