from django.urls import path

from pandora.ingest import views

urlpatterns = [
    path("ingest/am/", views.am_webhook, name="ingest-am"),
    path("api/<int:project_id>/envelope/", views.envelope, name="ingest-envelope"),
    path("api/<int:project_id>/store/", views.store, name="ingest-store"),
    path("api/<int:project_id>/logs/", views.logs, name="ingest-logs"),
    path("api/<int:project_id>/security/", views.security, name="ingest-security"),
    path("api/<int:project_id>/nel/", views.nel, name="ingest-nel"),
    path(
        "api/<int:project_id>/integration/otlp/v1/logs",
        views.otlp_logs,
        name="ingest-otlp-logs",
    ),
    path(
        "api/<int:project_id>/cron/<str:slug>/<str:sentry_key>/",
        views.check_in,
        name="ingest-check-in",
    ),
]
