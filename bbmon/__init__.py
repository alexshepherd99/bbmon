"""bbmon — home broadband monitoring.

Each service (pinger, speedtest, web, reboot) is a thin entrypoint importing
this shared package; the services communicate only via the SQLite database.
"""

__version__ = "0.1.0"
