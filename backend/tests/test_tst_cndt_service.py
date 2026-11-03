from __future__ import annotations

from datetime import date
import os

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.services.tst_cndt_service import collect_cndt_for_operation  # noqa: E402
from tests.fakes.postgrest import Postgrest  # noqa: E402


CNPJ = "04251091000199"


class Storage:
    def __init__(self):
        self.uploads = []

    def upload_document_at_key(self, key, content, mime):
        self.uploads.append((key, content, mime))
        return True


class Proxy:
    def __init__(self, result, pdf=b"%PDF-cndt"):
        self.result = result
        self.pdf = pdf
        self.calls = 0

    def issue(self, _cnpj):
        self.calls += 1
        return self.result, self.pdf


def _result(validity="31/12/2030"):
    return {
        "status": "obtida", "fonte": "tst", "valida": True,
        "resultado": "negativa", "cnpj_certidao": CNPJ,
        "data_emissao": "01/01/2030", "data_validade": validity,
        "numero_certidao": "123/2030", "filename": "cndt.pdf",
    }


def test_success_uploads_r2_writes_cache_lineage_and_snapshot():
    database = Postgrest({"operations": [{"id": "op-1", "cnpj": CNPJ}]})
    storage = Storage()
    proxy = Proxy(_result())

    outcome = collect_cndt_for_operation(
        "op-1", database=database, storage=storage, proxy=proxy, today=date(2030, 1, 2),
    )

    assert outcome.obtained is True
    assert proxy.calls == 1
    assert storage.uploads[0][0].startswith(f"clientes/{CNPJ}/documentos/")
    assert database.tables["component_snapshots"][0]["parsed_result"]["fonte"] == "tst"
    assert database.tables["component_snapshots"][0]["parsed_result"]["valida"] is True
    assert database.tables["certidoes_cndt_tst_lineage"][0]["cache_hit"] is False


def test_current_cache_reuses_r2_key_without_calling_vps():
    database = Postgrest({
        "operations": [{"id": "op-1", "cnpj": CNPJ}],
        "certidoes_cndt_tst_cache": [{
            "cnpj": CNPJ, "sha256": "a" * 64, "storage_key": "clientes/x/documentos/a.pdf",
            "filename": "cndt.pdf", "resultado": "negativa", "data_emissao": "2030-01-01",
            "data_validade": "2030-12-31", "numero_certidao": "1", "valida": True,
        }],
    })
    proxy = Proxy(_result())

    outcome = collect_cndt_for_operation(
        "op-1", database=database, storage=Storage(), proxy=proxy, today=date(2030, 1, 2),
    )

    assert outcome.obtained is True and outcome.source == "cache"
    assert proxy.calls == 0
    assert database.tables["certidoes_cndt_tst_lineage"][0]["cache_hit"] is True


def test_expired_cache_is_not_reused_and_collects_a_new_document():
    database = Postgrest({
        "operations": [{"id": "op-1", "cnpj": CNPJ}],
        "certidoes_cndt_tst_cache": [{
            "cnpj": CNPJ, "sha256": "a" * 64, "storage_key": "old.pdf", "resultado": "negativa",
            "data_validade": "2030-01-01", "valida": True,
        }],
    })
    proxy = Proxy(_result("31/12/2030"))

    outcome = collect_cndt_for_operation(
        "op-1", database=database, storage=Storage(), proxy=proxy, today=date(2030, 1, 10),
    )

    assert outcome.obtained is True
    assert proxy.calls == 1


def test_vps_failure_leaves_snapshot_absent_for_manual_fallback():
    database = Postgrest({"operations": [{"id": "op-1", "cnpj": CNPJ}]})
    proxy = Proxy({"status": "falha", "erro": "BLOQUEIO_ANTIBOT", "fonte": "tst"}, None)

    outcome = collect_cndt_for_operation(
        "op-1", database=database, storage=Storage(), proxy=proxy, today=date.today(),
    )

    assert outcome.obtained is False
    assert outcome.error == "BLOQUEIO_ANTIBOT"
    assert database.tables.get("component_snapshots", []) == []
