import re
import unicodedata


_ZERO_WIDTH = re.compile(r"[\u200b\u200c\u200d\ufeff]")
_DASHES = re.compile(r"[-‐‑‒–—―_]+")
_WHITESPACE = re.compile(r"\s+")
_PUNCTUATION = re.compile(r"[،,؛;.!؟?]+")


def normalize_operator_text(value: str) -> str:
    """Normalize only for full alias matching; it does not infer an ISP from prose."""
    normalized = unicodedata.normalize("NFKC", value)
    normalized = normalized.translate(str.maketrans({"ي": "ی", "ى": "ی", "ك": "ک"}))
    normalized = _ZERO_WIDTH.sub(" ", normalized)
    normalized = _DASHES.sub(" ", normalized)
    normalized = _PUNCTUATION.sub(" ", normalized)
    normalized = _WHITESPACE.sub(" ", normalized).strip().casefold()
    return normalized

