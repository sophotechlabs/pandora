from django.contrib import admin
from django.urls import include, path

from pandora.web.views import health, ready

urlpatterns = [
    path("admin/", admin.site.urls),
    path("health/", health, name="health"),
    path("ready/", ready, name="ready"),
    path("", include("pandora.ingest.urls")),
    path("", include("pandora.artifacts.urls")),
    path("api/v1/", include("pandora.web.api")),
    path("", include("django_prometheus.urls")),
    path("", include("pandora.ui.urls")),
]
