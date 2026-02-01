from django.urls import path, re_path

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    re_path(r"^api/(?P<path>.*)$", views.api_proxy, name="api_proxy"),
]
