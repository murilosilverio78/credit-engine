from __future__ import annotations

from datetime import date, timedelta
import json

from app.integrations.tst_cndt.client import CndtClient, CndtSettings, valid_cnpj
from app.integrations.tst_cndt.guardrails import GuardrailError, LocalGuardrails, mask_cnpj
from app.integrations.tst_cndt.models import CndtFailure, FailureCode
from app.integrations.tst_cndt.parser import parse_cndt_text


def _text(title: str, *, validity: str = "31/12/2030") -> str:
    return f"""TRIBUNAL SUPERIOR DO TRABALHO
{title}
CNPJ: 04.251.091/0001-99
Certidão nº: 123456/2030
Expedição: 01/07/2030
Validade: {validity}
Débitos Trabalhistas"""


def test_parser_negative_is_valid_independently_of_outcome():
    parsed = parse_cndt_text(_text("CERTIDÃO NEGATIVA DE DÉBITOS TRABALHISTAS"), "04251091000199", today=date(2030, 7, 1))
    assert parsed["resultado"] == "negativa"
    assert parsed["valida"] is True
    assert parsed["numero_certidao"] == "123456/2030"


def test_parser_positive_with_effects_is_valid_when_document_is_current():
    parsed = parse_cndt_text(_text("CERTIDÃO POSITIVA COM EFEITOS DE NEGATIVA"), "04251091000199", today=date(2030, 7, 1))
    assert parsed["resultado"] == "positiva_com_efeitos_negativa"
    assert parsed["valida"] is True


def test_parser_positive_is_not_conflated_with_validity():
    parsed = parse_cndt_text(_text("CERTIDÃO POSITIVA DE DÉBITOS TRABALHISTAS"), "04251091000199", today=date(2030, 7, 1))
    assert parsed["resultado"] == "positiva"
    assert parsed["valida"] is True


def test_failure_classification_never_contains_an_outcome():
    payload = CndtFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
    assert payload == {"status": "falha", "erro": "CAPTCHA_NAO_RESOLVIDO", "fonte": "tst"}
    assert "resultado" not in payload


def test_cnpj_validation_and_no_secret_in_invalid_output(tmp_path):
    assert valid_cnpj("04251091000199")
    result = CndtClient(CndtSettings(tmp_path)).issue("00.000.000/0000-00")
    encoded = json.dumps(result)
    assert result["erro"] == "CNPJ_INVALIDO"
    assert "TWOCAPTCHA_API_KEY" not in encoded
    assert "04251091000199" not in encoded


def test_unexpected_local_failure_does_not_escape_as_an_exception(tmp_path, monkeypatch):
    client = CndtClient(CndtSettings(tmp_path))
    monkeypatch.setattr(client.guardrails, "reusable", lambda _cnpj: (_ for _ in ()).throw(OSError("disk unavailable")))
    assert client.issue("04251091000199")["erro"] == "PORTAL_INDISPONIVEL"


def test_rate_limit_lock_cache_and_mask(tmp_path):
    guardrails = LocalGuardrails(tmp_path, max_attempts_per_minute=1)
    guardrails.consume_rate_slot(now=100)
    try:
        guardrails.consume_rate_slot(now=101)
        assert False, "expected a conservative global rate limit"
    except GuardrailError:
        pass
    cnpj = "04251091000199"
    with guardrails.cnpj_lock(cnpj):
        try:
            with guardrails.cnpj_lock(cnpj):
                assert False, "expected one concurrent issuance per CNPJ"
        except GuardrailError:
            pass
    pdf = tmp_path / "cndt.pdf"
    pdf.write_bytes(b"%PDF-test")
    expiry = (date.today() + timedelta(days=1)).strftime("%d/%m/%Y")
    guardrails.remember_success(cnpj, {"storage_key": str(pdf), "data_validade": expiry, "status": "obtida"})
    assert guardrails.reusable(cnpj)["storage_key"] == str(pdf)
    assert mask_cnpj(cnpj) == "***.***.***/****-99"


def test_captcha_wait_polls_locators_without_script_evaluation(tmp_path):
    from app.integrations.tst_cndt.client import CndtClient, CndtSettings

    class FakeLocator:
        def __init__(self, sources):
            self.sources = sources

        def get_attribute(self, name):
            assert name == "src"
            return self.sources.pop(0) if len(self.sources) > 1 else self.sources[0]

    class FakePage:
        def __init__(self, sources):
            self.locator_obj = FakeLocator(sources)

        def locator(self, selector):
            assert selector == "#captcha-imagem"
            return self.locator_obj

        def wait_for_function(self, *args, **kwargs):  # pragma: no cover
            raise AssertionError("string evaluation is forbidden by the portal CSP")

    client = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=1000))
    assert client._wait_for_captcha_image(FakePage(["", "data:image/png;base64,AAA"]))
    assert client._wait_for_captcha_image(FakePage(["data:image/png;base64,AAA", "data:image/png;base64,BBB"]), "data:image/png;base64,AAA")
    short = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=300))
    assert not short._wait_for_captcha_image(FakePage(["data:image/png;base64,AAA"]), "data:image/png;base64,AAA")
