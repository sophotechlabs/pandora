from django.contrib.auth import views as auth_views
from django.urls import path

from pandora.ui import views

app_name = "ui"

urlpatterns = [
    path("", views.stream, name="stream"),
    path("issues/<int:issue_id>/", views.issue_page, name="issue"),
    path("issues/<int:issue_id>/<str:tab>/", views.issue_page, name="issue-tab"),
    path("issues/actions/", views.issue_actions, name="issue-actions"),
    path("overview/", views.overview, name="overview"),
    path("ingest/", views.ingest, name="ingest"),
    path("ingest/replay/", views.replay_envelopes, name="ingest-replay"),
    path(
        "login/",
        auth_views.LoginView.as_view(
            template_name="ui/login.html",
            redirect_authenticated_user=True,
        ),
        name="login",
    ),
    path(
        "logout/",
        auth_views.LogoutView.as_view(next_page="ui:login"),
        name="logout",
    ),
]
