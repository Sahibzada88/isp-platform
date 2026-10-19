from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("invoices", views.InvoiceViewSet, basename="invoice")
router.register("payments", views.PaymentViewSet, basename="payment")
router.register("receipts", views.ReceiptViewSet, basename="receipt")
urlpatterns = [path("settings/billing/", views.BillingSettingsView.as_view())] + router.urls
