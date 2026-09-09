import pytest
from django.contrib.auth import models as auth_models

OPERATOR_PERMISSIONS = ("issues.change_issue",)


@pytest.fixture
def operator(db):
    user = auth_models.User.objects.create_user(
        username="operator",
        password="operator-pass",
        is_staff=True,
    )
    for label in OPERATOR_PERMISSIONS:
        app_label, codename = label.split(".")
        user.user_permissions.add(
            auth_models.Permission.objects.get(
                content_type__app_label=app_label,
                codename=codename,
            )
        )
    return user


@pytest.fixture
def operator_client(client, operator):
    client.force_login(operator)
    return client


@pytest.fixture
def make_user(db):
    def build(username, **overrides):
        fields = {
            "username": username,
            "password": f"{username}-pass",
            "is_staff": True,
        }
        fields.update(overrides)
        return auth_models.User.objects.create_user(**fields)

    return build
