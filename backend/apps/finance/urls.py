from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("corrections", views.CorrectionViewSet, basename="correction")
router.register("refunds", views.RefundViewSet, basename="refund")
router.register("overpayments", views.OverpaymentViewSet, basename="overpayment")
router.register("cash/deposits", views.DepositViewSet, basename="deposit")
urlpatterns = [path("cash/summary/", views.CashSummaryView.as_view())] + router.urls
