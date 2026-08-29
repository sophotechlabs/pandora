from __future__ import annotations

from typing import Any

from authlib.integrations.django_client import OAuth
from django.conf import settings
from django.contrib.auth import get_user_model

from pandora.people.models import Membership, Role, Team

PROVIDER = "pandora"


class OidcError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(
        settings.PANDORA_OIDC_ISSUER
        and settings.PANDORA_OIDC_CLIENT_ID
        and settings.PANDORA_OIDC_CLIENT_SECRET
    )


def metadata_url() -> str:
    issuer = settings.PANDORA_OIDC_ISSUER.rstrip("/")
    return f"{issuer}/.well-known/openid-configuration"


def client() -> Any:
    if not enabled():
        raise OidcError("OIDC is not configured")
    oauth = OAuth()
    oauth.register(
        name=PROVIDER,
        client_id=settings.PANDORA_OIDC_CLIENT_ID,
        client_secret=settings.PANDORA_OIDC_CLIENT_SECRET,
        server_metadata_url=metadata_url(),
        client_kwargs={"scope": settings.PANDORA_OIDC_SCOPES},
    )
    return oauth.create_client(PROVIDER)


def _claim_groups(claims: dict[str, Any]) -> list[str]:
    raw = claims.get(settings.PANDORA_OIDC_GROUPS_CLAIM) or []
    if isinstance(raw, str):
        raw = [part.strip() for part in raw.split(",") if part.strip()]
    return [str(group) for group in raw]


def _role_for(groups: list[str]) -> str | None:
    mapping = {
        Role.OWNER: settings.PANDORA_OIDC_OWNER_GROUP,
        Role.MEMBER: settings.PANDORA_OIDC_MEMBER_GROUP,
        Role.VIEWER: settings.PANDORA_OIDC_VIEWER_GROUP,
    }
    for role, group in mapping.items():
        if group and group in groups:
            return role
    if settings.PANDORA_OIDC_DEFAULT_ROLE:
        return settings.PANDORA_OIDC_DEFAULT_ROLE
    return None


def username_from(claims: dict[str, Any]) -> str:
    for name in ("preferred_username", "email", "sub"):
        value = claims.get(name)
        if value:
            return str(value)[:150]
    raise OidcError("the token carries no username, email or subject claim")


def provision(claims: dict[str, Any]) -> Any:
    model = get_user_model()
    username = username_from(claims)
    user, created = model.objects.get_or_create(
        username=username,
        defaults={"email": str(claims.get("email", ""))[:254], "is_staff": True},
    )
    changed = []
    if not user.is_staff:
        user.is_staff = True
        changed.append("is_staff")
    email = str(claims.get("email", ""))[:254]
    if email and user.email != email:
        user.email = email
        changed.append("email")
    if changed:
        user.save(update_fields=changed)
    if created:
        user.set_unusable_password()
        user.save(update_fields=["password"])
    _sync_team(user, claims)
    return user


def _sync_team(user: Any, claims: dict[str, Any]) -> None:
    role = _role_for(_claim_groups(claims))
    if role is None:
        return
    team, _ = Team.objects.get_or_create(name=settings.PANDORA_OIDC_TEAM)
    Membership.objects.update_or_create(team=team, user=user, defaults={"role": role})
