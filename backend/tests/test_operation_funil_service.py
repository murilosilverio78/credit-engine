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
        self.or_expression = None

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

    def or_(self, expression):
        self.or_expression = expression
        return self

    def execute(self):
        rows = [row.copy() for row in self.rows]
        for field, value, kind in self.filters:
            if kind == "eq":
                rows = [row for row in rows if row.get(field) == value]
            else:
                rows = [row for row in rows if row.get(field) in value]
        if self.or_expression:
            term = self.or_expression.split("*", 2)[1].lower()
            rows = [
                row for row in rows
                if term in str(row.get("cnpj") or "").lower()
                or term in str(row.get("razao_social") or "").lower()
            ]
        total = len(rows)
        end = None if self.end is None else self.end + 1
        return SimpleNamespace(data=rows[self.start:end], count=total)


class Rpc:
    def __init__(self, response):
        self.response = response

    def execute(self):
        return SimpleNamespace(data=self.response)


class Supabase:
    def __init__(self, tables, rpc_responses=None):
        self.tables = tables
        self.rpc_responses = rpc_responses or {}
        self.rpc_calls = []

    def table(self, name):
        return Query(self.tables.get(name, []))

    def rpc(self, name, params=None):
        self.rpc_calls.append((name, params))
        response = self.rpc_responses[name]
        return Rpc(response(params) if callable(response) else response)


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
    database = Supabase({}, {
        "listar_funil_operacoes": [{
            "cotacao_id": "C-1", "cnpj": "12345678000190", "nome_fornecedor": "Fornecedor",
            "valor_solicitado": 200_000, "margem_disponivel": 80_000,
            "saldo_vincendo": 150_000, "valor_enquadrado": 100_000,
            "tipo": "CONTRATO", "data_expiracao": "2026-10-03", "operation_id": "op-score",
            "estagio": "QUALIFICADA", "estagio_max": "QUALIFICADA",
            "estagio_motivo": "cobertura_insuficiente:1.61", "n_documentos": 3,
            "tipos_documento": ["contrato"], "estagio_atualizado_em": "2026-09-29T00:00:00Z",
            "created_at": "2026-09-01T00:00:00Z", "operation_created_at": "2026-09-01",
            "razao_social": "Fornecedor SA", "source": "x", "rating": "A", "score": 95,
            "taxa_sugerida": 0.02, "relatorio_gerado": True, "total_count": 2,
        }, {
            "cotacao_id": "C-2", "cnpj": "12345678000191", "nome_fornecedor": "Sem score",
            "operation_id": "op-sem-score", "estagio": "QUALIFICADA", "estagio_max": "QUALIFICADA",
            "created_at": "2026-09-02T00:00:00Z", "operation_created_at": "2026-09-02",
            "razao_social": "Sem score SA", "source": "x", "rating": "B", "score": 80,
            "taxa_sugerida": 0.03, "relatorio_gerado": False, "total_count": 2,
        }],
    })
    monkeypatch.setattr(service, "supabase", database)
    monkeypatch.setattr("app.services.eligibility_params_service.get_eligibility_config", lambda: PARAMS)
    monkeypatch.setattr(service.OperationService, "_funnel_summary", lambda _self: {"total_fila": 2, "estagios": {"QUALIFICADA": 2}, "relatorios_gerados": 1})

    result = asyncio.run(service.OperationService()._list_funil(
        estagio="QUALIFICADA", cnpj=None, busca=None, rating=None,
        relatorio=None, tipo_motivo=None, limit=20, offset=0,
    ))

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


def test_list_funil_forwards_server_filters_and_total_from_filtered_page(monkeypatch):
    filtered_row = {
        "cotacao_id": "C-41", "cnpj": "12345678000190", "nome_fornecedor": "Fora da primeira página",
        "operation_id": None, "estagio": "LISTA_ESPERA", "estagio_max": "LISTA_ESPERA",
        "created_at": "2026-09-01", "total_count": 1, "relatorio_gerado": False,
    }
    database = Supabase({}, {"listar_funil_operacoes": [filtered_row]})
    monkeypatch.setattr(service, "supabase", database)
    monkeypatch.setattr("app.services.eligibility_params_service.get_eligibility_config", lambda: PARAMS)
    monkeypatch.setattr(service.OperationService, "_funnel_summary", lambda _self: {})

    result = asyncio.run(service.OperationService()._list_funil(
        estagio="LISTA_ESPERA", cnpj=None, busca="Fora", rating=None,
        relatorio="pendente", tipo_motivo="criterio", limit=20, offset=0,
    ))

    assert result["total"] == 1
    assert result["items"][0]["cotacao_id"] == "C-41"
    assert database.rpc_calls == [("listar_funil_operacoes", {
        "p_estagio": "LISTA_ESPERA", "p_cnpj": None, "p_busca": "Fora",
        "p_rating": None, "p_relatorio": "pendente", "p_tipo_motivo": "criterio",
        "p_limit": 20, "p_offset": 0,
    })]


def test_list_manual_isolated_from_funnel_and_can_include_tests(monkeypatch):
    def manual_response(params):
        rows = [{"id": "manual-prod", "source": "admin_ui", "ambiente": "PRODUCAO", "cnpj": "1"}]
        if params["p_incluir_testes"]:
            rows.append({"id": "manual-test", "source": "admin_ui", "ambiente": "TESTE", "cnpj": "2"})
        return [{**row, "total_count": len(rows)} for row in rows]

    database = Supabase({}, {"listar_analises_manuais": manual_response})
    monkeypatch.setattr(service, "supabase", database)

    production = asyncio.run(service.OperationService().list_manual())
    all_environments = asyncio.run(service.OperationService().list_manual(incluir_testes=True))

    assert [item["id"] for item in production["items"]] == ["manual-prod"]
    assert [item["id"] for item in all_environments["items"]] == ["manual-prod", "manual-test"]


def test_list_manual_searches_before_pagination_and_counts_filtered_rows(monkeypatch):
    database = Supabase({}, {"listar_analises_manuais": [{
        "id": "later", "source": "admin_ui", "ambiente": "PRODUCAO",
        "cnpj": "2", "razao_social": "Alvo fora da primeira página", "total_count": 1,
    }]})
    monkeypatch.setattr(service, "supabase", database)

    result = asyncio.run(service.OperationService().list_manual(busca="Alvo", limit=1, offset=0))

    assert result["total"] == 1
    assert [item["id"] for item in result["items"]] == ["later"]
    assert database.rpc_calls[0] == ("listar_analises_manuais", {
        "p_incluir_testes": False, "p_cnpj": None, "p_busca": "Alvo",
        "p_limit": 1, "p_offset": 0,
    })


def test_funnel_summary_uses_database_aggregation_for_large_funnel(monkeypatch):
    database = Supabase({}, {"resumo_funil_operacoes": [{
        "total_fila": 1_501,
        "estagios": {"LISTA_ESPERA": 1_001, "QUALIFICADA": 500},
        "relatorios_gerados": 1_200,
    }]})
    monkeypatch.setattr(service, "supabase", database)

    summary = service.OperationService()._funnel_summary()

    assert summary == {
        "total_fila": 1_501,
        "estagios": {"LISTA_ESPERA": 1_001, "QUALIFICADA": 500},
        "relatorios_gerados": 1_200,
    }
    assert database.rpc_calls == [("resumo_funil_operacoes", None)]


def test_funnel_summary_returns_zeroes_when_there_are_no_quotes(monkeypatch):
    database = Supabase({}, {"resumo_funil_operacoes": [{
        "total_fila": 0,
        "estagios": {},
        "relatorios_gerados": 0,
    }]})
    monkeypatch.setattr(service, "supabase", database)

    assert service.OperationService()._funnel_summary() == {
        "total_fila": 0,
        "estagios": {},
        "relatorios_gerados": 0,
    }


def test_funnel_summary_returns_null_fields_when_database_fails(monkeypatch):
    class FailingSupabase:
        def rpc(self, _name):
            raise RuntimeError("database unavailable")

    monkeypatch.setattr(service, "supabase", FailingSupabase())

    assert service.OperationService()._funnel_summary() == {
        "total_fila": None,
        "estagios": None,
        "relatorios_gerados": None,
    }
