"""Every registered source provider, by name. Add a source with one line here."""
from __future__ import annotations

from .base import SourceProvider
from .skillsfuture import SkillsFutureProvider
from .tag_context import TagContextProvider

PROVIDERS: dict[str, SourceProvider] = {
    provider.name: provider for provider in (SkillsFutureProvider(), TagContextProvider())
}


def get_provider(name: str) -> SourceProvider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"No source provider {name!r}; registered: {sorted(PROVIDERS)}") from exc
