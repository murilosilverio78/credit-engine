from app.services.policy.inputs import montar_entrada
from app.services.findings.version import EMITTER_VERSION
from tests.fakes.postgrest import Postgrest


def test_inputs_use_latest_current_emitter_run_per_specialist_only():
    db = Postgrest({
        "operations": [{"id": "op", "valor_enquadrado": 10, "valor_solicitado": 20, "pct_max_contrato": .2}],
        "finding_runs": [
            {"id": "old", "operation_id": "op", "especialista": "sacado_orgao", "versao_emissor": "3", "status": "COMPLETO", "created_at": "2026-01-02"},
            {"id": "new", "operation_id": "op", "especialista": "sacado_orgao", "versao_emissor": EMITTER_VERSION, "status": "PARCIAL", "created_at": "2026-01-01"},
        ],
        "findings": [{"run_id": "new", "codigo": "contratos_ativos_qtd", "valor": 2, "estado": "CONFIRMADO", "confianca": "ALTA"}],
    })
    entrada = montar_entrada("op", database=db)
    assert entrada.findings["contratos_ativos_qtd"].valor == 2
    assert entrada.runs_usados["sacado_orgao"]["run_id"] == "new"
    assert entrada.runs_usados["sacado_orgao"]["status"] == "PARCIAL"
    assert "cadastro_regularidade" in entrada.indisponiveis
