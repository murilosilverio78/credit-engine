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


def test_debug_diagnostics_are_off_by_default_and_mask_long_digit_runs(tmp_path):
    from app.integrations.tst_cndt.client import CndtClient, CndtSettings

    quiet = CndtClient(CndtSettings(output_dir=tmp_path))
    quiet._diag("x", valor=1)
    assert quiet.diagnostics == []
    verbose = CndtClient(CndtSettings(output_dir=tmp_path, debug=True))
    verbose._diag("x", amostra=verbose._mask_digits("CNPJ 04251091000199 emitido"))
    assert verbose.diagnostics[0]["amostra"] == "CNPJ *** emitido"


class _Resp:
    def __init__(self, url, content_type, body=b"", payload=None, status=200):
        self.url = url
        self.headers = {"content-type": content_type}
        self._body = body
        self._payload = payload
        self.status = status

    def body(self):
        return self._body

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


class _Download:
    def __init__(self, path):
        self._path = path
        self.suggested_filename = "certidao.pdf"

    def path(self):
        return self._path


class _Page:
    def __init__(self, on_click):
        self.handlers = {}
        self.on_click = on_click

    def on(self, name, handler):
        self.handlers[name] = handler

    def remove_listener(self, name, handler):
        self.handlers.pop(name, None)

    def locator(self, selector):
        assert selector == "#botao-emitir"
        page = self

        class _Button:
            def click(self):
                page.on_click(page)

        return _Button()

    def wait_for_timeout(self, ms):
        pending = getattr(self, "pending", [])
        while pending:
            name, value = pending.pop(0)
            self.handlers[name](value)


def test_collect_reads_browser_download_when_response_body_is_empty(tmp_path):
    from app.integrations.tst_cndt.client import CndtClient, CndtSettings

    pdf = tmp_path / "saved.pdf"
    pdf.write_bytes(b"%PDF-1.4 conteudo")

    def click(page):
        page.pending = [
            ("response", _Resp("https://x/api/certidao", "application/pdf", b"")),
            ("download", _Download(str(pdf))),
        ]

    client = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=2000))
    kind, payload, hint = client._submit_and_collect(_Page(click), 1)
    assert kind == "pdf" and payload.startswith(b"%PDF") and "certidao.pdf" in hint


def test_collect_reads_pdf_from_response_body(tmp_path):
    from app.integrations.tst_cndt.client import CndtClient, CndtSettings

    def click(page):
        page.pending = [("response", _Resp("https://x/api/certidao", "application/pdf", b"%PDF-1.4 x"))]

    client = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=2000))
    kind, payload, _ = client._submit_and_collect(_Page(click), 1)
    assert kind == "pdf" and payload.startswith(b"%PDF")


def test_collect_reports_error_message_and_empty_pdf(tmp_path):
    from app.integrations.tst_cndt.client import CndtClient, CndtSettings

    def click_error(page):
        page.pending = [("response", _Resp("https://x/api/certidao", "application/json", b"{}", {"erro": "Captcha incorreto"}, 400))]

    client = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=2000))
    kind, payload, _ = client._submit_and_collect(_Page(click_error), 1)
    assert kind == "message" and "Captcha" in payload

    def click_empty(page):
        page.pending = [("response", _Resp("https://x/api/certidao", "application/pdf", b""))]

    short = CndtClient(CndtSettings(output_dir=tmp_path, portal_timeout_ms=12000))
    import app.integrations.tst_cndt.client as module

    original = module.time.monotonic
    ticks = iter(range(0, 1000))
    module.time.monotonic = lambda: float(next(ticks))
    try:
        kind, _, _ = short._submit_and_collect(_Page(click_empty), 1)
    finally:
        module.time.monotonic = original
    assert kind == "pdf_vazio"
