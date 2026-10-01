"""ASGI application entry point."""

try:
    from core import app
    import watch_store as _watch_store
    from routes.auth_routes import register as register_auth_routes
    from routes.catalog_routes import register as register_catalog_routes
    from routes.frontend_routes import register as register_frontend_routes
    from routes.media_routes import register as register_media_routes
    from routes.watch_routes import register as register_watch_routes
except ImportError:
    from .core import app
    from . import watch_store as _watch_store
    from .routes.auth_routes import register as register_auth_routes
    from .routes.catalog_routes import register as register_catalog_routes
    from .routes.frontend_routes import register as register_frontend_routes
    from .routes.media_routes import register as register_media_routes
    from .routes.watch_routes import register as register_watch_routes


register_auth_routes()
register_catalog_routes()
register_media_routes()
register_watch_routes()
register_frontend_routes()
