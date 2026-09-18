"""Pure provider-field normalization; retain the original news receipt.

This does not fetch data, infer a timestamp, invent an article body or count
duplicate syndication as independent reporting. A missing field stays missing.
"""
from __future__ import annotations

from copy import deepcopy


def normalize_news(items, provider):
    """Return copied news rows with known canonical fields added if present.

    Massive describes an article with ``description`` (an excerpt, not its full
    body), ``article_url`` and ``published_utc``. FMP already uses the canonical
    names. Original identifiers, publisher metadata and aliases are preserved.
    Existing nonempty canonical values take precedence over aliases.
    """
    if not isinstance(items, list):
        return []
    aliases = {"text": "description", "url": "article_url",
               "publishedDate": "published_utc"} if str(provider).upper() in {"MASSIVE", "POLYGON"} else {}
    result = []
    for item in items:
        if not isinstance(item, dict):
            continue
        row = deepcopy(item)
        for field, alias in aliases.items():
            if not row.get(field) and row.get(alias) is not None and row.get(alias) != "":
                row[field] = deepcopy(row[alias])
        result.append(row)
    return result
