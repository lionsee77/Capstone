"""What is specific to one external source, kept out of the generic pipeline code.

One module per source, each a `SourceProvider` (see `base.py`) registered in
`registry.py`; enrichment asks the registry rather than importing a source.
"""
