from app.normalization import normalize_operator_text
from app.operator_defaults import DEFAULT_OPERATORS


def test_default_aliases_are_unique_after_normalization() -> None:
    owners: dict[str, str] = {}
    for canonical_name, variants in DEFAULT_OPERATORS.items():
        for alias in (canonical_name, *variants):
            normalized = normalize_operator_text(alias)
            existing_owner = owners.setdefault(normalized, canonical_name)
            # Duplicate spellings inside one operator are fine; a collision between
            # two different operators would make matching ambiguous.
            assert existing_owner == canonical_name
