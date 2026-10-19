from rest_framework.routers import DefaultRouter

from .views import AttachmentViewSet

router = DefaultRouter()
router.register("attachments", AttachmentViewSet, basename="attachment")
urlpatterns = router.urls
