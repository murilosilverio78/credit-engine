from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from app.integrations.caixa_fgts.client import CrfClient, CrfSettings
from app.integrations.caixa_fgts.guardrails import FgtsGuardrails, GuardrailError
from app.integrations.caixa_fgts.models import BotBlockedError
from app.integrations.caixa_fgts.parser import is_invalid_captcha, parse_crf_html


FIXTURES = Path(__file__).parent / "fixtures" / "caixa_fgts"
CNPJ = "04251091000199"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_parser_extracts_regular_result_fixture():
    parsed = parse_crf_html(fixture("regular.html"), CNPJ, today=date(2030, 1, 1))
    assert parsed == {
        "situacao": "regular",
        "numero_certificado": "202401010101",
        "data_emissao": "01/01/2024",
        "data_inicio_validade": "01/01/2024",
        "data_fim_validade": "31/12/2030",
        "cnpj_certidao": CNPJ,
        "razao_social": "ACME SERVICOS LTDA",
        "valida": True,
    }


def test_parser_marks_irregular_fixture_without_a_pdf():
    parsed = parse_crf_html(fixture("irregular.html"), CNPJ)
    assert parsed["situacao"] == "irregular"
    assert parsed["numero_certificado"] is None


def test_captcha_invalid_fixture_is_detected():
    assert is_invalid_captcha(fixture("captcha_invalido.html"))


def test_block_fixture_raises_safe_diagnostic(tmp_path):
    class Page:
        def content(self):
            return fixture("bloqueio.html")

        def title(self):
            return "Access Denied - Imperva"

    with pytest.raises(BotBlockedError) as caught:
        CrfClient(CrfSettings(tmp_path))._check_antibot(Page(), 403)
    diagnostic = caught.value.diagnostic
    assert diagnostic["status"] == 403
    assert diagnostic["title"] == "Access Denied - Imperva"
    assert CNPJ not in diagnostic["html_excerpt"]


def test_debug_snapshot_sanitizes_captcha_and_cnpj(tmp_path):
    class Page:
        def screenshot(self, *, path, full_page):
            assert full_page is True
            Path(path).write_bytes(b"png")

        def content(self):
            return f'<input name="captcha" value="secret-answer"><p>{CNPJ}</p>'

    client = CrfClient(CrfSettings(tmp_path, debug=True))
    client._snapshot(Page(), "01 teste")
    saved = (tmp_path / "01-teste.html").read_text(encoding="utf-8")
    assert "secret-answer" not in saved
    assert CNPJ not in saved
    assert (tmp_path / "01-teste.png").read_bytes() == b"png"


def test_fgts_guardrails_rate_lock_cache_and_namespace(tmp_path):
    guardrails = FgtsGuardrails(tmp_path, max_attempts_per_minute=1)
    guardrails.consume_rate_slot(now=100)
    with pytest.raises(GuardrailError):
        guardrails.consume_rate_slot(now=101)
    with guardrails.cnpj_lock(CNPJ):
        with pytest.raises(GuardrailError):
            with guardrails.cnpj_lock(CNPJ):
                pass
    pdf = tmp_path / "crf.pdf"
    pdf.write_bytes(b"%PDF-test")
    valid_until = (date.today() + timedelta(days=6)).strftime("%d/%m/%Y")
    guardrails.remember_success(CNPJ, {"storage_key": str(pdf), "data_fim_validade": valid_until, "status": "obtida"})
    assert guardrails.reusable(CNPJ, safety_days=5)["storage_key"] == str(pdf)
    assert (tmp_path / ".caixa-fgts-rate.json").is_file()
    assert (tmp_path / ".caixa-fgts-cache.json").is_file()


def test_cli_uses_requested_arguments_and_handles_irregular(tmp_path, monkeypatch, capsys):
    import app.integrations.caixa_fgts.__main__ as cli

    observed = {}

    class FakeClient:
        diagnostics = []

        def __init__(self, settings):
            observed["settings"] = settings

        def issue(self, cnpj, uf):
            observed.update(cnpj=cnpj, uf=uf)
            return {"status": "irregular", "situacao": "irregular"}

    monkeypatch.setattr(cli, "CrfClient", FakeClient)
    assert cli.main(["--cnpj", CNPJ, "--uf", "SP", "--out", str(tmp_path)]) == 0
    assert observed["cnpj"] == CNPJ and observed["uf"] == "SP"
    assert observed["settings"].output_dir == tmp_path
    assert '"status": "irregular"' in capsys.readouterr().out


def test_cli_serializes_bot_block_as_a_failure(tmp_path, monkeypatch, capsys):
    import app.integrations.caixa_fgts.__main__ as cli

    class FakeClient:
        diagnostics = []

        def __init__(self, settings):
            pass

        def issue(self, cnpj, uf):
            raise BotBlockedError(status=403, title="Denied", html_excerpt="Access denied")

    monkeypatch.setattr(cli, "CrfClient", FakeClient)
    assert cli.main(["--cnpj", CNPJ, "--uf", "SP", "--out", str(tmp_path)]) == 2
    assert "BLOQUEIO_ANTIBOT" in capsys.readouterr().out
