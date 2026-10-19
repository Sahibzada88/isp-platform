"""Customer-portal authentication. Deliberately separate from staff auth:
different session key, different user object, different permission class."""
from django.middleware.csrf import CsrfViewMiddleware
from rest_framework import exceptions
from rest_framework.authentication import BaseAuthentication
from rest_framework.permissions import BasePermission

from .models import CustomerAccount

SESSION_KEY = "portal_account_id"


class PortalUser:
    is_authenticated = True
    is_active = True
    is_anonymous = False

    def __init__(self, account):
        self.account = account
        self.customer = account.customer
        self.pk = account.pk


class PortalSessionAuthentication(BaseAuthentication):
    def authenticate(self, request):
        pk = request._request.session.get(SESSION_KEY)
        if not pk:
            return None
        account = CustomerAccount.objects.select_related("customer").filter(pk=pk, is_active=True).first()
        if account is None:
            return None
        self._enforce_csrf(request)
        return PortalUser(account), None

    def authenticate_header(self, request):
        return "Session"

    @staticmethod
    def _enforce_csrf(request):
        check = CsrfViewMiddleware(lambda r: None)
        check.process_request(request._request)
        reason = check.process_view(request._request, None, (), {})
        if reason:
            raise exceptions.PermissionDenied(f"CSRF Failed: {reason}")


class IsPortalCustomer(BasePermission):
    def has_permission(self, request, view):
        return isinstance(request.user, PortalUser)
