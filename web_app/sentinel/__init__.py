from .app import create_blueprint

sentinel_api = create_blueprint()

__all__ = ["sentinel_api"]
