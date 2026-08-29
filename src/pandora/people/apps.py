from django.apps import AppConfig


class PeopleConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pandora.people"

    def ready(self) -> None:
        from pandora.people import signals  # noqa: F401
