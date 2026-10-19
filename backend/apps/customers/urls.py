from rest_framework.routers import DefaultRouter

from . import transfer_views, views

router = DefaultRouter()
router.register("customers", views.CustomerViewSet, basename="customer")
router.register("connections", views.ConnectionViewSet, basename="connection")
router.register("transfers/office", transfer_views.OfficeTransferViewSet, basename="office-transfer")
router.register("transfers/ownership", transfer_views.OwnershipTransferViewSet, basename="ownership-transfer")
urlpatterns = router.urls
