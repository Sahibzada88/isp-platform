from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("portal/connections", views.ConnectionViewSet, basename="portal-connection")
router.register("portal/invoices", views.InvoiceViewSet, basename="portal-invoice")
router.register("portal/payments", views.PaymentViewSet, basename="portal-payment")
router.register("portal/receipts", views.ReceiptViewSet, basename="portal-receipt")
router.register("portal/complaints", views.ComplaintViewSet, basename="portal-complaint")
router.register("portal-accounts", views.AccountViewSet, basename="portal-account")
urlpatterns = [
    path("portal/auth/login/", views.PortalLoginView.as_view()),
    path("portal/auth/logout/", views.PortalLogoutView.as_view()),
    path("portal/auth/me/", views.PortalMeView.as_view()),
    path("portal/auth/change-password/", views.PortalPasswordView.as_view()),
] + router.urls
