import pytest

from app.normalization import normalize_operator_text


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (" همراه‌اول ", "همراه اول"),
        ("همراه---اول", "همراه اول"),
        ("HAMRAH  AVAL", "hamrah aval"),
        ("ايرانسل", "ایرانسل"),
        (" IrAnCeLl ", "irancell"),
        ("پارس‌آنلاین", "پارس آنلاین"),
    ],
)
def test_normalize_operator_text(value: str, expected: str) -> None:
    assert normalize_operator_text(value) == expected


def test_prose_is_not_an_alias() -> None:
    assert normalize_operator_text("من از همراه اول استفاده میکنم") != normalize_operator_text("همراه اول")

