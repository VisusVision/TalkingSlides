
from __future__ import annotations

from rest_framework import permissions

from core.models import Project


def is_staff_user(user) -> bool:
    return bool(user and (getattr(user, "is_staff", False) or getattr(user, "is_superuser", False)))


def is_verified_publisher_or_teacher(user) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_staff_user(user):
        return True
    profile = getattr(user, "profile", None)
    return bool(profile and profile.role in {"teacher", "publisher"})


def can_manage_project_moderation(user, project: Project) -> bool:
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if is_staff_user(user):
        return True
    return bool(
        project.user_id
        and int(project.user_id) == int(user.id)
        and is_verified_publisher_or_teacher(user)
    )


class IsStaffUser(permissions.BasePermission):
    def has_permission(self, request, view) -> bool:
        return is_staff_user(getattr(request, "user", None))
