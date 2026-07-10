"""WSGI entry for production servers (gunicorn, waitress)."""
from app import app

application = app
