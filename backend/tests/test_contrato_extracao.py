import io
import os
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace

import pytest


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import contract_extraction_params_service  # noqa: E402
from app.workers.tasks import contrato_extracao  # noqa: E402


class FakeBroadfactorClient:
    def __init__(self, content: bytes):
        self.content = content

    def contratos_da_cotacao(self, cotacao_id):
        assert cotacao_id == "C-1"
        return [SimpleNamespace(numero_contrato="0001", arquivo="arquivo.pdf")]

    def baixar_contrato(self, cotacao_id, numero_contrato):
        assert (cotacao_id, numero_contrato) == ("C-1", "0001")
        return self.content


def _params(**overrides):
    params = {
        "ocr_max_pages": 15.0,
        "timeout_total_seconds": 300.0,
        "llm_max_chars": 60_000.0,
    }
    params.update(overrides)
    return params


def _llm_payload(**overrides):
    payload = {
        "valor_global": 1_000_000,
        "data_inicio_vigencia": "2026-01-01",
        "data_fim_vigencia": "2027-01-01",
        "prazo_vigencia_meses": 12,
        "objeto_contratual": "Servicos continuados",
        "regime_conta_vinculada": "CONTA_DEPOSITO_VINCULADA",
        "orgao_contratante": "Orgao Federal",
        "numero_contrato": "0001",
    }
    payload.update(overrides)
    return payload


def test_document_signature_detects_pdf_and_zip_by_bytes():
    pdf = b"%PDF-1.7\nsynthetic"
    assert contrato_extracao._normalize_document(pdf) == (pdf, "PDF", None)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("readme.txt", "not a pdf")
        archive.writestr("nested/contract.bin", pdf)

    extracted, source_type, internal_name = contrato_extracao._normalize_document(
        buffer.getvalue()
    )
    assert extracted == pdf
    assert source_type == "ZIP"
    assert internal_name == "nested/contract.bin"


def test_unknown_document_type_fails_with_signature():
    with pytest.raises(contrato_extracao.ContractDocumentError) as exc_info:
        contrato_extracao._normalize_document(b"not-a-contract")

    assert exc_info.value.reason == "tipo_arquivo_desconhecido"
    assert exc_info.value.details["assinatura_hex"] == b"not-a-co".hex()


def test_llm_text_selection_keeps_relevant_late_page_under_cap():
    pages = [(f"pagina {index} " + "x" * 700) for index in range(12)]
    pages[10] = "clausula de vigencia e conta vinculada " + "y" * 700

    text, selected_pages, truncated = contrato_extracao._select_llm_text(
        pages,
        max_chars=4_000,
    )

    assert truncated is True
    assert selected_pages[:4] == [1, 2, 3, 4]
    assert 11 in selected_pages
    assert "conta vinculada" in text
    assert len(text) <= 4_000


def test_ocr_is_serialized_across_concurrent_operations(monkeypatch):
    state = {"active": 0, "max_active": 0}
    state_lock = threading.Lock()

    def fake_locked(_content, _max_pages, _deadline):
        with state_lock:
            state["active"] += 1
            state["max_active"] = max(state["max_active"], state["active"])
        time.sleep(0.05)
        with state_lock:
            state["active"] -= 1
        return ["texto"], 1, False

    monkeypatch.setattr(contrato_extracao, "_ocr_pdf_locked", fake_locked)

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                contrato_extracao._ocr_pdf,
                b"%PDF",
                15,
                contrato_extracao._Deadline.start(2),
            )
            for _ in range(2)
        ]
        assert [future.result() for future in futures] == [
            (["texto"], 1, False),
            (["texto"], 1, False),
        ]

    assert state["max_active"] == 1


def test_scanned_pdf_limits_ocr_pages_and_sets_truncation_flag(monkeypatch):
    monkeypatch.setattr(
        contrato_extracao,
        "get_contract_extraction_config",
        lambda: _params(ocr_max_pages=15),
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_load_operation",
        lambda _operation_id: {
            "cotacao_id": "C-1",
            "saldo_vincendo": 2_000_000,
            "prazo_final_meses": 12,
        },
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_extract_pdf_pages",
        lambda _content, **_kwargs: [""] * 20,
    )
    seen = {}

    def fake_ocr(_content, max_pages, deadline):
        assert deadline.remaining("test") > 0
        seen["max_pages"] = max_pages
        return ["texto reconhecido " * 40] * max_pages, 20, True

    monkeypatch.setattr(contrato_extracao, "_ocr_pdf", fake_ocr)
    monkeypatch.setattr(
        contrato_extracao,
        "_llm_extract",
        lambda *_args, **_kwargs: _llm_payload(),
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_persist_term",
        lambda *_args, **_kwargs: (4, 4),
    )

    result = contrato_extracao._fetch(
        "12345678000100",
        operation_id="op-1",
        broadfactor_client=FakeBroadfactorClient(b"%PDF-1.7\nscan"),
    )

    assert result["status_extracao"] == "EXTRAIDO"
    assert result["metodo_texto"] == "OCR"
    assert result["paginas_ocr"] == 15
    assert seen["max_pages"] == 15
    assert "documento_truncado_para_ocr" in result["flags"]
    assert result["razao_saldo_vincendo_valor_global"] == 2.0
    assert "saldo_vincendo_divergente_valor_global" in result["flags"]


def test_component_timeout_is_recorded_without_raising(monkeypatch):
    monkeypatch.setattr(
        contrato_extracao,
        "get_contract_extraction_config",
        lambda: _params(),
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_load_operation",
        lambda _operation_id: {"cotacao_id": "C-1", "saldo_vincendo": 700_000},
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_extract_pdf_pages",
        lambda _content, **_kwargs: ["texto " * 200],
    )
    monkeypatch.setattr(
        contrato_extracao,
        "_llm_extract",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("limite")),
    )

    result = contrato_extracao._fetch(
        "12345678000100",
        operation_id="op-1",
        broadfactor_client=FakeBroadfactorClient(b"%PDF-1.7\ntext"),
    )

    assert result["status_extracao"] == "NAO_EXTRAIDO"
    assert result["motivo"] == "timeout_extracao_contrato"
    assert "limite" in result["error"]


def test_llm_uses_forced_structured_tool_output():
    payload = _llm_payload()

    class FakeMessages:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                content=[SimpleNamespace(type="tool_use", input=payload)]
            )

    fake_client = SimpleNamespace(messages=FakeMessages())
    result = contrato_extracao._llm_extract(
        "texto suficiente",
        "0001",
        contrato_extracao._Deadline.start(5),
        client=fake_client,
    )

    assert result == payload
    assert fake_client.messages.kwargs["tool_choice"] == {
        "type": "tool",
        "name": "registrar_contrato",
    }
    assert fake_client.messages.kwargs["model"] == contrato_extracao.settings.CLAUDE_MODEL_EXTRACTION


def test_remaining_term_is_conservative_calendar_months():
    assert contrato_extracao._remaining_months(date(2027, 3, 4), date(2026, 9, 4)) == 6
    assert contrato_extracao._remaining_months(date(2027, 3, 3), date(2026, 9, 4)) == 5
    assert contrato_extracao._remaining_months(date(2026, 9, 20), date(2026, 9, 4)) == 0


def test_persist_term_updates_existing_operation_fields(monkeypatch):
    updates = []

    class Query:
        def update(self, payload):
            updates.append(payload)
            return self

        def eq(self, *_args):
            return self

        def execute(self):
            return SimpleNamespace(data=[{"id": "op-1"}])

    fake_supabase = SimpleNamespace(table=lambda _name: Query())
    monkeypatch.setattr("app.core.database.supabase", fake_supabase)
    monkeypatch.setattr(
        "app.services.eligibility_params_service.get_eligibility_config",
        lambda: {"prazo_padrao_meses": 12},
    )

    prazo_vincendo, prazo_final = contrato_extracao._persist_term(
        "op-1",
        {"prazo_final_meses": 12},
        {"data_fim_vigencia": "2027-02-28"},
        date(2026, 9, 4),
    )

    assert prazo_vincendo == 5
    assert prazo_final == 5
    assert updates == [
        {
            "prazo_vincendo_meses": 5,
            "prazo_final_meses": 5,
            "prazo_vincendo_indisponivel": False,
        }
    ]


def test_contract_parameter_service_has_safe_defaults_before_migration(monkeypatch):
    contract_extraction_params_service.invalidate_cache()
    monkeypatch.setattr(
        contract_extraction_params_service,
        "_load_from_db",
        lambda: None,
    )

    assert contract_extraction_params_service.get_contract_extraction_config() == (
        contract_extraction_params_service.DEFAULT_PARAMS
    )
