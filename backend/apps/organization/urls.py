from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("offices", views.OfficeViewSet, basename="office")
router.register("villages", views.VillageViewSet, basename="village")
urlpatterns = router.urls
