from django.urls import path, re_path
from django.views.generic import TemplateView

from . import views


urlpatterns = [
    path("", views.index, name="index"),
    path("mocks", TemplateView.as_view(template_name="dashboard/mocks.html"), name="mocks"),
    re_path(r"^api/(?P<path>.*)$", views.api_proxy, name="api_proxy"),
]
