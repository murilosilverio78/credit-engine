import sys
from types import ModuleType, SimpleNamespace

from app.services.ingestion_discard_service import record_ingestion_discard


class FakeQuery:
    def __init__(self):
        self.inserted = None

    def insert(self, data):
        self.inserted = data
        return self

    def execute(self):
        return SimpleNamespace(data=[self.inserted])


class FakeSupabase:
    def __init__(self):
        self.table_name = None
        self.query = FakeQuery()

    def table(self, name):
        self.table_name = name
        return self.query


def test_record_ingestion_discard_persists_calibration_fields(monkeypatch):
    fake_supabase = FakeSupabase()
    fake_database = ModuleType("app.core.database")
    fake_database.supabase = fake_supabase
    monkeypatch.setitem(sys.modules, "app.core.database", fake_database)

    record_ingestion_discard(
        cotacao_id="000123",
        cnpj="03012610000101",
        valor_solicitado=8_000_000,
        margem_disponivel=12_000_000,
        valor_enquadrado=6_000_000,
        motivo="Valor enquadrado acima do ticket maximo.",
        estagio="elegibilidade",
    )

    assert fake_supabase.table_name == "descartes_ingestao"
    assert fake_supabase.query.inserted == {
        "cotacao_id": "000123",
        "cnpj": "03012610000101",
        "valor_solicitado": 8_000_000,
        "margem_disponivel": 12_000_000,
        "valor_enquadrado": 6_000_000,
        "motivo": "Valor enquadrado acima do ticket maximo.",
        "estagio": "elegibilidade",
    }
