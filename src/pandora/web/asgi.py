import os

from django.core.asgi import get_asgi_application

from pandora.web import telemetry

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pandora.web.settings")

telemetry.configure()

application = get_asgi_application()
