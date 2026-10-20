import json
import os
from types import SimpleNamespace


for key in ("SECRET_KEY", "TWOCAPTCHA_API_KEY", "RESEND_API_KEY"):
    os.environ.setdefault(key, "test")

from app.workers.tasks import score_engine  # noqa: E402


def _snapshots(*, receipt_count: int = 1) -> dict:
    receipts = [
        {
            "orgao": f"Órgão {index % 25}",
            "codigo_orgao": str(index % 25),
            "valor": 10_000 + index,
            "competencia": "10/2026",
        }
        for index in range(receipt_count)
    ]
    return {
        "brasil_api": {"razao_social": "Empresa teste", "porte": "EPP"},
        "pessoa_juridica": {"situacao": "ATIVA"},
        "contratos": {
            "total_contratos": 2,
            "contratos_ativos": 1,
            "valor_total_ativo": 500_000,
            "valor_total_historico": 700_000,
            "orgaos_contratantes": ["Órgão 1", "Órgão 2"],
            "contratos_detalhe": [
                {
                    "orgao": "Órgão 1", "numero": "01/2026", "objeto": "Serviços contínuos",
                    "valor_inicial": 500_000, "data_inicio": "2026-01-01",
                    "data_fim": "2026-12-31", "situacao": "Vigente",
                }
            ],
        },
        "recursos_recebidos": {
            "total_registros": receipt_count,
            "recursos_detalhe": receipts,
            "serie_anual": {"2026": sum(item["valor"] for item in receipts)},
            "valor_por_ano": {"2026": sum(item["valor"] for item in receipts)},
            "concentracao": {"hhi": 1000},
            "volatilidade": {"cv": 0.1},
            "periodo_inicio": "01/2026", "periodo_fim": "10/2026",
            "reconciliacao": {"status": "CONVERGENTE"},
        },
    }


def test_porte_context_compacts_five_thousand_receipts_below_hard_limit():
    snapshots = _snapshots(receipt_count=5_000)
    payload, serialized = score_engine._porte_payload(
        snapshots, score_engine._faturamento_context(snapshots),
    )

    assert len(serialized) / 3.5 <= score_engine.LLM_CONTEXT_HARD_TOKENS
    assert "recursos_detalhe" not in serialized
    assert len(payload["recursos_recebidos"]["top_orgaos_por_valor"]) <= 10
    assert payload["recursos_recebidos"]["total_registros"] == 5_000


def test_porte_llm_small_context_keeps_result_parity():
    class Messages:
        async def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps({
                "nivel": "Adequado", "fatores": ["Estrutura compatível"], "flags": [],
                "raciocinio": "Porte adequado.",
            }))])

    messages = Messages()
    client = SimpleNamespace(messages=messages)
    result = score_engine.score_porte_llm(
        "12345678000199", _snapshots(), client=client, operation_id="op-1",
    )

    prompt = messages.kwargs["messages"][0]["content"]
    assert result["nivel"] == "Adequado"
    assert result["score"] == score_engine.NIVEL_NOTA["Adequado"]
    assert result["fatores"] == ["Estrutura compatível"]
    assert "recursos_detalhe" not in prompt
    assert "\n  \"" not in prompt
