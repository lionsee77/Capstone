"""Every registered source provider, by name. Add a source with one line here."""
from __future__ import annotations

from .base import SourceProvider
from .tag_context import TagContextProvider

PROVIDERS: dict[str, SourceProvider] = {
    provider.name: provider for provider in (TagContextProvider(),)
}

# stores provider instances by name:
# {
#     "tag_context": TagContextProvider(),
# }
# so adding a provider should require a new module and one registry entry

def get_provider(name: str) -> SourceProvider:
    try:
        return PROVIDERS[name]
    except KeyError as exc:
        raise ValueError(f"No source provider {name!r}; registered: {sorted(PROVIDERS)}") from exc
