from .models import SystemSetting


def get_setting(key, default=None):
    row = SystemSetting.objects.filter(key=key).first()
    return row.value if row else default


def set_setting(key, value):
    SystemSetting.objects.update_or_create(key=key, defaults={"value": value})
