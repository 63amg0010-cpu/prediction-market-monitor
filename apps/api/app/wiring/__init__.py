"""Production composition helpers for the FastAPI application."""

from .dependencies import dependencies_from_environment
from .dependency_models import AppDependencies
from .routes import include_application_routes

__all__ = [
    "AppDependencies",
    "dependencies_from_environment",
    "include_application_routes",
]
