from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("imports", views.ImportViewSet, basename="import")
router.register("opening-balances", views.OpeningBalanceViewSet, basename="opening-balance")
urlpatterns = router.urls
