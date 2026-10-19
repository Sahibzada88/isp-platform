"""DRF helpers: deny-by-default permission map + office-scoped querysets."""
from django.http import Http404
from rest_framework.permissions import BasePermission

from . import services


class PermissionMap(BasePermission):
    """View declares `permission_map = {action: code}`. Unmapped actions are denied."""

    def has_permission(self, request, view):
        user = request.user
        if not (user and user.is_authenticated and user.is_active):
            return False
        code = getattr(view, "permission_map", {}).get(getattr(view, "action", None))
        return bool(code) and services.has_permission(user, code)


class OfficeScopedMixin:
    """Filters querysets to offices where the user holds the *current action's* permission.
    Out-of-scope objects behave as 404 (no existence leak)."""

    office_field = "office_id"

    def current_code(self):
        return self.permission_map[self.action]

    def scope(self, code=None):
        return services.office_scope(self.request.user, code or self.current_code())

    def scoped(self, queryset, code=None):
        return self.scope(code).apply(queryset, self.office_field)

    def check_office(self, office_id, code=None):
        if office_id not in self.scope(code):
            raise Http404
