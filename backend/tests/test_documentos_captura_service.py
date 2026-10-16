import os

for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.integrations.broadfactor.client import (  # noqa: E402
    DocumentoAnexo,
    DocumentoBaixado,
    DocumentDownloadError,
    QuotationInactiveError,
)
from app.services.documentos_captura_service import (  # noqa: E402
    capturar_documentos_cotacao,
)
from tests.fakes.postgrest import Postgrest  # noqa: E402


CONFIG = {
    "tipos_capturados": {"FATURAMENTO", "ULTIMO_BALANCO"},
    "tamanho_max_bytes": 100,
    "max_tentativas": 3,
}


class FakeClient:
    def __init__(self, documents, downloads):
        self.documents = documents
        self.downloads = downloads
        self.download_calls = []

    def documentos_da_cotacao(self, _cotacao_id):
        if isinstance(self.documents, Exception):
            raise self.documents
        return self.documents

    def baixar_documento(self, document_id):
        self.download_calls.append(document_id)
        value = self.downloads[document_id]
        if isinstance(value, Exception):
            raise value
        return value


class FakeStorage:
    def __init__(self):
        self.calls = []

    def upload_document_at_key(self, key, content, mime):
        self.calls.append((key, content, mime))
        return True


def doc(document_id, tipo="FATURAMENTO", dono="EMPRESA"):
    return DocumentoAnexo(tipo=tipo, arquivo=f"{document_id}.pdf", id=document_id, dono=dono)


def downloaded(content=b"pdf", nome="arquivo.pdf", mime="application/pdf"):
    return DocumentoBaixado(nome=nome, mime=mime, conteudo=content)


def capture(database, client, storage):
    return capturar_documentos_cotacao(
        "C-1", "03.012.610/0001-01", "00000000-0000-0000-0000-000000000001",
        database=database, broadfactor_client=client, storage=storage, config=CONFIG,
    )


def test_filters_types_and_records_owner_kind():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient(
        [doc("empresa"), doc("socio", dono="Maria"), doc("ignorar", "CPF")],
        {"empresa": downloaded(b"a"), "socio": downloaded(b"b")},
    )
    storage = FakeStorage()

    result = capture(database, client, storage)

    assert result == {"capturados": 2, "reaproveitados": 0, "falhas": 0, "indisponiveis": 0, "ignorados": 0}
    assert client.download_calls == ["empresa", "socio"]
    assert [row["dono_tipo"] for row in database.tables["documentos_cliente"]] == ["EMPRESA", "SOCIO"]


def test_idempotency_skips_existing_captured_document():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient([doc("doc-1")], {"doc-1": downloaded(b"same")})
    storage = FakeStorage()

    capture(database, client, storage)
    result = capture(database, client, storage)

    assert result == {"capturados": 0, "reaproveitados": 0, "falhas": 0, "indisponiveis": 0, "ignorados": 0}
    assert client.download_calls == ["doc-1"]
    assert len(storage.calls) == 1


def test_reuses_cnpj_hash_without_second_upload():
    database = Postgrest({"documentos_cliente": []})
    storage = FakeStorage()
    first = FakeClient([doc("first")], {"first": downloaded(b"same")})
    second = FakeClient([doc("second")], {"second": downloaded(b"same")})

    capture(database, first, storage)
    result = capture(database, second, storage)

    assert result["reaproveitados"] == 1
    assert len(storage.calls) == 1
    assert database.tables["documentos_cliente"][-1]["storage_key"] == database.tables["documentos_cliente"][0]["storage_key"]


def test_size_limit_is_terminal_and_does_not_upload():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient([doc("large")], {"large": downloaded(b"x" * 101)})
    storage = FakeStorage()

    result = capture(database, client, storage)

    assert result["ignorados"] == 1
    assert storage.calls == []
    assert database.tables["documentos_cliente"][0]["status"] == "IGNORADO_TAMANHO"


def test_failure_isolated_and_inactive_marks_remaining_documents():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient(
        [doc("bad"), doc("inactive"), doc("later")],
        {
            "bad": RuntimeError("upstream"),
            "inactive": QuotationInactiveError("QUOTATION_INACTIVE"),
            "later": downloaded(b"later"),
        },
    )
    storage = FakeStorage()

    result = capture(database, client, storage)

    assert result["falhas"] == 1
    assert result["indisponiveis"] == 2
    assert {row["status"] for row in database.tables["documentos_cliente"]} == {"FALHA", "INDISPONIVEL"}
    assert storage.calls == []


def test_failure_reason_contains_download_code_and_http_status():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient(
        [doc("forbidden")],
        {"forbidden": DocumentDownloadError("falha_download_documento", status=403)},
    )

    result = capture(database, client, FakeStorage())

    assert result["falhas"] == 1
    assert database.tables["documentos_cliente"][0]["erro"] == (
        "DocumentDownloadError:falha_download_documento:403"
    )


def test_failure_reason_includes_network_error_type_without_payload():
    database = Postgrest({"documentos_cliente": []})
    client = FakeClient(
        [doc("timeout")],
        {"timeout": DocumentDownloadError("timeout", error_type="ReadTimeout")},
    )

    result = capture(database, client, FakeStorage())

    assert result["falhas"] == 1
    assert database.tables["documentos_cliente"][0]["erro"] == (
        "DocumentDownloadError:timeout:None:ReadTimeout"
    )
