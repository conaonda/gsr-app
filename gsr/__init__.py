"""GSR local-first video registration service."""

__version__ = "0.1.0"

from .server import app, create_app

__all__ = ["app", "create_app", "__version__"]
