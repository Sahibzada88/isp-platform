from django.apps import AppConfig


class OperationsConfig(AppConfig):
    name = "apps.operations"

    def ready(self):
        from . import services  # noqa: F401  (registers the employee_disabled receiver)
