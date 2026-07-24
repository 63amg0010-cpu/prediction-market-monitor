"""Vercel Python entrypoint for the stateless FastAPI application."""

from app.main import app

__all__ = ["app"]
