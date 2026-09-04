from django.apps import AppConfig


class AttachmentsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pandora.attachments"

    def ready(self) -> None:
        from pandora.attachments import signals

        signals.connect()
