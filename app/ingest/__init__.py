__all__ = ["refresh_source"]


def __getattr__(name: str):
    if name == "refresh_source":
        from app.ingest.pipeline import refresh_source

        return refresh_source
    raise AttributeError(name)
