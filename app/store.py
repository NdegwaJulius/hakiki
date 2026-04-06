"""
Shared in-memory data store.
Populated during app lifespan; mutated by the admin upload handler.
Imported by main.py, app/routers/cbk.py, and app/routers/web.py.
"""

SACCOS: list[dict] = []
CBK_ENTITIES: list[dict] = []


def reload_saccos(records: list[dict]) -> None:
    SACCOS.clear()
    SACCOS.extend(records)


def reload_cbk(records: list[dict]) -> None:
    """Replace ALL CBK entities (all 5 categories at once)."""
    CBK_ENTITIES.clear()
    CBK_ENTITIES.extend(records)


def reload_cbk_category(category: str, records: list[dict]) -> None:
    """Replace only the records for one entity type, keep others intact."""
    surviving = [e for e in CBK_ENTITIES if e["category"] != category]
    CBK_ENTITIES.clear()
    CBK_ENTITIES.extend(surviving)
    CBK_ENTITIES.extend(records)
