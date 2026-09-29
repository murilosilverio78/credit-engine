import os

import pytest
from pydantic import ValidationError


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.api.v1.endpoints.operations import PropostaInput  # noqa: E402


def test_manual_operation_accepts_six_digit_uasg():
    payload = PropostaInput(
        cnpj="14.757.507/0001-07",
        origem_dados="MANUAL",
        uasg="200344",
    )

    assert payload.uasg == "200344"


def test_manual_operation_rejects_invalid_uasg():
    with pytest.raises(ValidationError, match="UASG deve ter 6 dígitos"):
        PropostaInput(
            cnpj="14.757.507/0001-07",
            origem_dados="MANUAL",
            uasg="123",
        )
