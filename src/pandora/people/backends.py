from __future__ import annotations

from typing import Any

from django.contrib.auth.backends import BaseBackend

from pandora.people import access


class TeamRoleBackend(BaseBackend):
    def authenticate(self, request: Any, **credentials: Any) -> None:
        return None

    def get_all_permissions(self, user_obj: Any, obj: Any = None) -> set[str]:
        if obj is not None:
            return set()
        return set(access.permissions_of(user_obj))

    def has_perm(self, user_obj: Any, perm: str, obj: Any = None) -> bool:
        return perm in self.get_all_permissions(user_obj, obj)
