from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from pandora.core.models import Project
from pandora.people.models import ROLE_ORDER, ROLE_PERMISSIONS, Membership

User = AbstractBaseUser


def role_of(user: User | AnonymousUser) -> str | None:
    if not getattr(user, "is_authenticated", False):
        return None
    roles = list(Membership.objects.filter(user=user).values_list("role", flat=True))
    if not roles:
        return None
    return max(roles, key=lambda role: ROLE_ORDER.get(role, 0))


def permissions_of(user: User | AnonymousUser) -> tuple[str, ...]:
    role = role_of(user)
    if role is None:
        return ()
    return ROLE_PERMISSIONS.get(role, ())


def projects_for(user: User | AnonymousUser) -> list[int] | None:
    if getattr(user, "is_superuser", False):
        return None
    teams = Membership.objects.filter(user=user).values_list("team_id", flat=True)
    if not teams:
        return None
    scoped = list(
        Project.objects.filter(teams__in=list(teams))
        .distinct()
        .values_list("pk", flat=True)
    )
    if not scoped:
        return None
    return scoped


def may(user: User | AnonymousUser, permission: str) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if getattr(user, "has_perm", None) and user.has_perm(permission):
        return True
    return permission in permissions_of(user)
