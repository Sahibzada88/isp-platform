from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("api/v1/", include("apps.accounts.urls")),
    path("api/v1/", include("apps.organization.urls")),
    path("api/v1/", include("apps.packages.urls")),
    path("api/v1/", include("apps.customers.urls")),
    path("api/v1/", include("apps.billing.urls")),
    path("api/v1/", include("apps.finance.urls")),
    path("api/v1/", include("apps.core.urls")),
    path("api/v1/", include("apps.notifications.urls")),
    path("api/v1/", include("apps.operations.urls")),
    path("api/v1/", include("apps.hr.urls")),
    path("api/v1/", include("apps.inventory.urls")),
    path("api/v1/", include("apps.expenses.urls")),
    path("api/v1/", include("apps.leads.urls")),
    path("api/v1/", include("apps.media.urls")),
    path("api/v1/", include("apps.portal.urls")),
    path("api/v1/", include("apps.reports.urls")),
    path("api/v1/", include("apps.imports.urls")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/v1/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]
