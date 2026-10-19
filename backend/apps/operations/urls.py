from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("complaints", views.ComplaintViewSet, basename="complaint")
router.register("tasks", views.TaskViewSet, basename="task")
urlpatterns = router.urls
