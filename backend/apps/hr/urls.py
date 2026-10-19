from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("attendance", views.AttendanceViewSet, basename="attendance")
router.register("leave", views.LeaveViewSet, basename="leave")
urlpatterns = router.urls
