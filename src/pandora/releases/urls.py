from django.urls import path

from pandora.releases import views

urlpatterns = [
    path(
        "api/0/organizations/<str:organization>/releases/<path:version>/deploys/",
        views.create_deploy,
        name="releases-create-deploy",
    ),
]
