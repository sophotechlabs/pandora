from django.apps import AppConfig


class PerfConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pandora.perf"
    label = "perf"
    verbose_name = "Performance"
