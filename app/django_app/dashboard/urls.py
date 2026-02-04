from django.urls import path, re_path

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    path("healthz/", views.healthz, name="healthz"),
    path("secret-drop/", views.secret_drop, name="secret_drop"),
    re_path(r"^api/(?P<path>.*)$", views.api_proxy, name="api_proxy"),
]
