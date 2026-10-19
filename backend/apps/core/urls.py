from django.urls import path

from .views import BackupRequestView, ExpireConnectionsCronView

urlpatterns = [
    path("system/backups/", BackupRequestView.as_view()),
    path("jobs/expire-connections/", ExpireConnectionsCronView.as_view(), name="job-expire-connections"),
]
