from django.urls import path

from pandora.artifacts import views

urlpatterns = [
    path(
        "api/0/organizations/<str:organization>/chunk-upload/",
        views.chunk_upload,
        name="artifacts-chunk-upload",
    ),
]
