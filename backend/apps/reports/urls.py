from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("reports", views.ReportViewSet, basename="report")
router.register("audit-logs", views.AuditLogViewSet, basename="audit-log")
urlpatterns = [path("dashboard/", views.DashboardView.as_view())] + router.urls
