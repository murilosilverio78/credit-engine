import os
from datetime import date

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services import certidoes_broadfactor_service as service  # noqa: E402
from app.services.upload_service import _is_current_validity  # noqa: E402
from app.workers.tasks.score_engine import _certidao_estado  # noqa: E402
from tests.fakes.postgrest import Postgrest  # noqa: E402


CNPJ = "03012610000101"


class Storage:
    def __init__(self, documents):
        self.documents = documents

    def download_document_at_key(self, key):
        return self.documents[key]


def _patch_pdf_text(monkeypatch, text):
    monkeypatch.setattr(service, "normalize_document", lambda _content: (b"%PDF", "PDF", None))
    monkeypatch.setattr(service, "extract_pdf_pages", lambda *_args: [text])


def test_deterministic_federal_positive_with_effects_is_valid_without_model(monkeypatch):
    _patch_pdf_text(monkeypatch, """
        CERTIDÃO POSITIVA COM EFEITOS DE NEGATIVA
        Receita Federal do Brasil - Tributos Federais
        CNPJ: 03.012.610/0001-01
        Emissão: 01/10/2026
        Validade: 31/12/2026
        Código de Controle: ABCD.1234.EFGH
    """)

    extraction = service.extract_certificate(
        b"pdf", "CND_FEDERAL", CNPJ, today=date(2026, 10, 28),
        model_fallback=lambda *_args: (_ for _ in ()).throw(AssertionError("modelo nao deve ser chamado")),
    )

    assert extraction["resultado"] == "positiva_com_efeitos_negativa"
    assert extraction["valida"] is True
    assert extraction["codigo_controle"] == "ABCD.1234.EFGH"
    assert _certidao_estado(extraction)[0] == "positiva_com_efeitos_negativa"


def test_positive_certificate_keeps_positive_state_instead_of_not_validated(monkeypatch):
    _patch_pdf_text(monkeypatch, """
        CERTIDÃO POSITIVA
        Receita Federal - Tributos Federais
        CNPJ: 03.012.610/0001-01
        Emissão: 01/10/2026
        Validade: 31/12/2026
    """)

    extraction = service.extract_certificate(b"pdf", "CND_FEDERAL", CNPJ, today=date(2026, 10, 28))

    assert extraction["valida"] is True
    assert _certidao_estado(extraction)[0] == "positiva"


def test_manual_validity_check_is_independent_from_certificate_outcome():
    assert _is_current_validity("31/12/2099") is True
    assert _is_current_validity("01/01/2000") is False


def test_state_and_municipal_certificates_are_extracted_as_informational(monkeypatch):
    estadual = """
        CERTIDÃO NEGATIVA DE DÉBITOS
        SECRETARIA DA FAZENDA DO ESTADO DE SÃO PAULO
        CNPJ: 03.012.610/0001-01
        Emissão: 01/10/2026
        Validade: 31/12/2026
        UF: SP
    """
    municipal = """
        CERTIDÃO NEGATIVA DE DÉBITOS MUNICIPAIS
        PREFEITURA MUNICIPAL DE SÃO PAULO
        CNPJ: 03.012.610/0001-01
        Emissão: 01/10/2026
        Validade: 31/12/2026
    """
    _patch_pdf_text(monkeypatch, estadual)
    state_result = service.extract_certificate(b"pdf", "CND_ESTADUAL", CNPJ, today=date(2026, 10, 28))
    _patch_pdf_text(monkeypatch, municipal)
    city_result = service.extract_certificate(b"pdf", "CND_MUNICIPAL", CNPJ, today=date(2026, 10, 28))

    assert state_result["uf"] == "SP"
    assert state_result["valida"] is True
    assert city_result["municipio"] == "SÃO PAULO"
    assert city_result["valida"] is True


def test_expired_certificate_is_not_valid_but_preserves_expired_state(monkeypatch):
    _patch_pdf_text(monkeypatch, """
        CERTIDÃO NEGATIVA - Receita Federal - Tributos Federais
        CNPJ: 03.012.610/0001-01
        Emissão: 01/01/2026
        Validade: 01/02/2026
    """)

    extraction = service.extract_certificate(b"pdf", "CND_FEDERAL", CNPJ, today=date(2026, 10, 28))

    assert extraction["valida"] is False
    assert _certidao_estado(extraction)[0] == "vencida"


def test_processor_materializes_only_federal_snapshot_and_keeps_other_types_informational(monkeypatch):
    text_by_content = {
        b"federal": """
            CERTIDÃO NEGATIVA - Receita Federal - Tributos Federais
            CNPJ: 03.012.610/0001-01
            Emissão: 01/10/2026
            Validade: 31/12/2026
        """,
        b"state": """
            CERTIDÃO NEGATIVA - SECRETARIA DA FAZENDA DO ESTADO
            CNPJ: 03.012.610/0001-01
            Emissão: 01/10/2026
            Validade: 31/12/2026
            UF: SP
        """,
        b"city": """
            CERTIDÃO NEGATIVA - PREFEITURA MUNICIPAL DE SÃO PAULO
            CNPJ: 03.012.610/0001-01
            Emissão: 01/10/2026
            Validade: 31/12/2026
        """,
    }
    monkeypatch.setattr(service, "normalize_document", lambda content: (content, "PDF", None))
    monkeypatch.setattr(service, "extract_pdf_pages", lambda content, *_args: [text_by_content[content]])
    database = Postgrest({
        "cotacoes_broadfactor": [{"cotacao_id": "C-1", "estagio": "DOCUMENTADA"}],
        "documentos_cliente": [
            {"id": "a", "cotacao_id": "C-1", "cnpj": CNPJ, "tipo": "CND_FEDERAL", "sha256": "a" * 64, "storage_key": "f", "nome_arquivo": "f.pdf", "status": "CAPTURADO"},
            {"id": "b", "cotacao_id": "C-1", "cnpj": CNPJ, "tipo": "CND_ESTADUAL", "sha256": "b" * 64, "storage_key": "s", "nome_arquivo": "s.pdf", "status": "CAPTURADO"},
            {"id": "c", "cotacao_id": "C-1", "cnpj": CNPJ, "tipo": "CND_MUNICIPAL", "sha256": "c" * 64, "storage_key": "m", "nome_arquivo": "m.pdf", "status": "CAPTURADO"},
        ],
        "component_snapshots": [],
    })

    result = service.processar_certidoes_cotacao(
        "C-1", CNPJ, "op-1", database=database,
        storage=Storage({"f": b"federal", "s": b"state", "m": b"city"}),
        today=date(2026, 10, 28),
    )

    assert result == {"processadas": 3, "reaproveitadas": 0, "falhas": 0, "informativas": 2}
    assert {row["tipo"] for row in database.tables["certidoes_broadfactor"]} == {
        "CND_FEDERAL", "CND_ESTADUAL", "CND_MUNICIPAL",
    }
    snapshots = database.tables["component_snapshots"]
    assert len(snapshots) == 1
    assert snapshots[0]["component"] == "cnd_federal"
    assert snapshots[0]["parsed_result"]["fonte"] == "broadfactor"
    assert snapshots[0]["parsed_result"]["valida"] is True


def test_processor_reuses_cache_without_download(monkeypatch):
    database = Postgrest({
        "cotacoes_broadfactor": [{"cotacao_id": "C-1", "estagio": "DOCUMENTADA"}],
        "documentos_cliente": [{"id": "a", "cotacao_id": "C-1", "cnpj": CNPJ, "tipo": "CND_FEDERAL", "sha256": "a" * 64, "storage_key": "f", "nome_arquivo": "f.pdf", "status": "CAPTURADO"}],
        "certidoes_broadfactor": [{"tipo": "CND_FEDERAL", "cnpj": CNPJ, "sha256": "a" * 64, "resultado": "negativa", "data_emissao": "2026-10-01", "data_validade": "2026-12-31", "codigo_controle": None, "orgao_emissor": "Receita Federal", "valida": True, "fonte_extracao": "deterministico", "confianca": "ALTA"}],
        "component_snapshots": [],
    })

    result = service.processar_certidoes_cotacao(
        "C-1", CNPJ, "op-1", database=database,
        storage=Storage({}), today=date(2026, 10, 28),
    )

    assert result == {"processadas": 0, "reaproveitadas": 1, "falhas": 0, "informativas": 0}
    assert database.tables["component_snapshots"][0]["parsed_result"]["resultado"] == "negativa"


def test_cached_certificate_that_has_expired_no_longer_suppresses_manual_upload():
    database = Postgrest({
        "cotacoes_broadfactor": [{"cotacao_id": "C-1", "estagio": "DOCUMENTADA"}],
        "documentos_cliente": [{"id": "a", "cotacao_id": "C-1", "cnpj": CNPJ, "tipo": "CND_FEDERAL", "sha256": "a" * 64, "storage_key": "f", "nome_arquivo": "f.pdf", "status": "CAPTURADO"}],
        "certidoes_broadfactor": [{"tipo": "CND_FEDERAL", "cnpj": CNPJ, "sha256": "a" * 64, "resultado": "negativa", "data_emissao": "2026-01-01", "data_validade": "2026-02-01", "codigo_controle": None, "orgao_emissor": "Receita Federal", "valida": True, "fonte_extracao": "deterministico", "confianca": "ALTA"}],
        "component_snapshots": [],
    })

    service.processar_certidoes_cotacao(
        "C-1", CNPJ, "op-1", database=database, storage=Storage({}), today=date(2026, 10, 28),
    )

    parsed = database.tables["component_snapshots"][0]["parsed_result"]
    assert parsed["valida"] is False
    assert _certidao_estado(parsed)[0] == "vencida"


def test_processor_refuses_closed_quotation_without_reading_historical_document():
    database = Postgrest({
        "cotacoes_broadfactor": [{"cotacao_id": "C-closed", "estagio": "ENCERRADA"}],
        "documentos_cliente": [{"id": "a", "cotacao_id": "C-closed", "cnpj": CNPJ, "tipo": "CND_FEDERAL", "sha256": "a" * 64, "storage_key": "f", "nome_arquivo": "f.pdf", "status": "CAPTURADO"}],
    })

    result = service.processar_certidoes_cotacao(
        "C-closed", CNPJ, "op-1", database=database, storage=Storage({}),
    )

    assert result == {"processadas": 0, "reaproveitadas": 0, "falhas": 0, "informativas": 0}
    assert database.tables.get("certidoes_broadfactor", []) == []
