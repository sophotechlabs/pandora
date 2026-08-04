import os

from django.core.wsgi import get_wsgi_application

from pandora.web import telemetry

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "pandora.web.settings")

telemetry.configure()

application = get_wsgi_application()
