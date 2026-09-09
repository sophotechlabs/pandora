from django.urls import path

from pandora.releases import views

urlpatterns = [
    path(
        "api/0/organizations/<str:organization>/releases/",
        views.releases,
        name="releases-create",
    ),
    path(
        "api/0/organizations/<str:organization>/releases/<path:version>/deploys/",
        views.create_deploy,
        name="releases-create-deploy",
    ),
    path(
        "api/0/organizations/<str:organization>/releases/<path:version>/",
        views.release_detail,
        name="releases-detail",
    ),
]
