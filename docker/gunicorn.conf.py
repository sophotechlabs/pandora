import os

logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "json": {"()": "pandora.core.log.JsonFormatter"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "json"},
    },
    "root": {"handlers": ["console"], "level": "WARNING"},
    "loggers": {
        "gunicorn.error": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


def child_exit(server, worker):
    if not os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        return
    from prometheus_client import multiprocess

    multiprocess.mark_process_dead(worker.pid)
