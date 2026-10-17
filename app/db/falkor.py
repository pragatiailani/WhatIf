from functools import lru_cache

from falkordb import FalkorDB, Graph

from app.config import get_settings


@lru_cache
def get_client() -> FalkorDB:
    settings = get_settings()
    return FalkorDB(host=settings.falkor_host, port=settings.falkor_port)


def select_graph(name: str) -> Graph:
    return get_client().select_graph(name)
