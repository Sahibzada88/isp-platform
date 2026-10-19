from rest_framework.routers import DefaultRouter

from . import views

router = DefaultRouter()
router.register("inventory/items", views.ItemViewSet, basename="inventory-item")
router.register("inventory/movements", views.MovementViewSet, basename="inventory-movement")
router.register("inventory/purchases", views.PurchaseViewSet, basename="inventory-purchase")
router.register("inventory/usages", views.UsageViewSet, basename="inventory-usage")
router.register("inventory/adjustments", views.AdjustmentViewSet, basename="inventory-adjustment")
router.register("inventory/returns", views.ReturnViewSet, basename="inventory-return")
router.register("inventory/equipment", views.EquipmentViewSet, basename="inventory-equipment")
urlpatterns = router.urls
