import asyncio
import os
from types import SimpleNamespace


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")
os.environ.setdefault("ANTHROPIC_API_KEY", "test")
os.environ.setdefault("PORTAL_TRANSPARENCIA_TOKEN", "test")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://user:pass@localhost/test")
os.environ.setdefault("SUPABASE_URL", "http://localhost")
os.environ.setdefault(
    "SUPABASE_SERVICE_KEY",
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJyb2xlIjoic2VydmljZV9yb2xlIiwiaXNzIjoic3VwYWJhc2UifQ.testsignature",
)

from app.services import operation_service as service  # noqa: E402


class Query:
    def __init__(self, rows):
        self.rows = rows
        self.filters = []
        self.start = 0
        self.end = None

    def select(self, *_args, **_kwargs):
        return self

    def eq(self, field, value):
        self.filters.append((field, value, "eq"))
        return self

    def in_(self, field, values):
        self.filters.append((field, values, "in"))
        return self

    def order(self, *_args, **_kwargs):
        return self

    def range(self, start, end):
        self.start, self.end = start, end
        return self

    def execute(self):
        rows = [row.copy() for row in self.rows]
        for field, value, kind in self.filters:
            if kind == "eq":
                rows = [row for row in rows if row.get(field) == value]
            else:
                rows = [row for row in rows if row.get(field) in value]
        total = len(rows)
        end = None if self.end is None else self.end + 1
        return SimpleNamespace(data=rows[self.start:end], count=total)


class Supabase:
    def __init__(self, tables):
        self.tables = tables

    def table(self, name):
        return Query(self.tables.get(name, []))


PARAMS = {
    "ticket_minimo": 100_000,
    "ticket_maximo": 5_000_000,
    "dias_minimos_expiracao": 5,
    "funil_cobertura_min": 2,
    "funil_hist_min_meses": 6,
    "funil_orgaos_min": 2,
}


def test_mapear_motivos_funil_uses_parameters_and_keeps_unknown_code():
    motivos = service.mapear_motivos_funil(
        "abaixo_ticket_minimo; cobertura_insuficiente:1.61; "
        "indisponibilidade_fonte:ceis; codigo_novo",
        PARAMS,
    )

    assert motivos[0]["rotulo"] == "Abaixo do ticket mínimo (R$ 100 mil)"
    assert motivos[1]["rotulo"] == "Cobertura 1,61x (mínimo 2x)"
    assert motivos[2]["tipo"] == "indisponibilidade"
    assert motivos[2]["rotulo"] == "Sanções não verificadas (fonte indisponível: CEIS)"
    assert motivos[3] == {
        "codigo": "codigo_novo",
        "rotulo": "Critério não atendido: codigo novo",
        "detalhe": "Motivo recebido do funil sem rótulo específico.",
        "tipo": "criterio",
    }


def test_list_funil_exposes_quote_fields_and_only_completed_score_report(monkeypatch):
    database = Supabase({
        "cotacoes_broadfactor": [{
            "cotacao_id": "C-1", "cnpj": "12345678000190", "nome_fornecedor": "Fornecedor",
            "valor_solicitado": 200_000, "margem_disponivel": 80_000,
            "saldo_vincendo": 150_000, "valor_enquadrado": 100_000,
            "tipo": "CONTRATO", "data_expiracao": "2026-10-03", "operation_id": "op-score",
            "estagio": "QUALIFICADA", "estagio_max": "QUALIFICADA",
            "estagio_motivo": "cobertura_insuficiente:1.61", "n_documentos": 3,
            "tipos_documento": ["contrato"], "estagio_atualizado_em": "2026-09-29T00:00:00Z",
            "created_at": "2026-09-01T00:00:00Z", "ambiente": "PRODUCAO",
        }, {
            "cotacao_id": "C-2", "cnpj": "12345678000191", "nome_fornecedor": "Sem score",
            "operation_id": "op-sem-score", "estagio": "QUALIFICADA", "estagio_max": "QUALIFICADA",
            "created_at": "2026-09-02T00:00:00Z", "ambiente": "PRODUCAO",
        }],
        "operations": [
            {"id": "op-score", "rating": "A", "score": 95, "taxa_sugerida": 0.02, "source": "x", "created_at": "2026-09-01", "razao_social": "Fornecedor SA"},
            {"id": "op-sem-score", "rating": "B", "score": 80, "taxa_sugerida": 0.03, "source": "x", "created_at": "2026-09-02", "razao_social": "Sem score SA"},
        ],
        "component_snapshots": [{"operation_id": "op-score", "component": "score_engine", "status": "completed"}],
    })
    monkeypatch.setattr(service, "supabase", database)
    monkeypatch.setattr("app.services.eligibility_params_service.get_eligibility_config", lambda: PARAMS)
    monkeypatch.setattr(service.OperationService, "_funnel_summary", lambda _self: {"total_fila": 2, "estagios": {"QUALIFICADA": 2}, "relatorios_gerados": 1})

    result = asyncio.run(service.OperationService()._list_funil(estagio="QUALIFICADA", cnpj=None, limit=20, offset=0))

    scored, unscored = result["items"]
    assert scored["margem_disponivel"] == 80_000
    assert scored["saldo_vincendo"] == 150_000
    assert scored["tipo"] == "CONTRATO"
    assert scored["data_expiracao"] == "2026-10-03"
    assert scored["estagio_max"] == "QUALIFICADA"
    assert scored["n_documentos"] == 3
    assert scored["motivos"][0]["codigo"] == "cobertura_insuficiente:1.61"
    assert scored["relatorio"] == {
        "gerado": True, "rating": "A", "score": 95, "taxa_sugerida": 0.02, "operation_id": "op-score",
    }
    assert unscored["relatorio"] is None
    assert "status" not in scored
    assert "score" not in scored


def test_list_manual_isolated_from_funnel_and_can_include_tests(monkeypatch):
    database = Supabase({
        "operations": [
            {"id": "manual-prod", "source": "admin_ui", "ambiente": "PRODUCAO", "cnpj": "1"},
            {"id": "manual-test", "source": "admin_ui", "ambiente": "TESTE", "cnpj": "2"},
            {"id": "funil", "source": "broadfactor_ingestao", "ambiente": "PRODUCAO", "cnpj": "3"},
        ],
    })
    monkeypatch.setattr(service, "supabase", database)

    production = asyncio.run(service.OperationService().list_manual())
    all_environments = asyncio.run(service.OperationService().list_manual(incluir_testes=True))

    assert [item["id"] for item in production["items"]] == ["manual-prod"]
    assert [item["id"] for item in all_environments["items"]] == ["manual-prod", "manual-test"]
