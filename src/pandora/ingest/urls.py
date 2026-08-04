from django.urls import path

from pandora.ingest import views

urlpatterns = [
    path("ingest/am/", views.am_webhook, name="ingest-am"),
    path("api/<int:project_id>/envelope/", views.envelope, name="ingest-envelope"),
]
