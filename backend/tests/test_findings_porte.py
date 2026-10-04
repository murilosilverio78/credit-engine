from app.services.findings.adapters.porte import emit_porte
from app.services.findings.schemas import Estado


def test_porte_adapter_uses_potential_persisted_score_dimension_when_available():
    absent = emit_porte({}, fingerprint="x")[0]
    present = emit_porte({"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Critico", "nivel_potencial": "Adequado"}}}}, fingerprint="x")[0]
    assert absent.estado == Estado.NAO_VERIFICADO
    assert present.valor == "Adequado"
    assert present.confianca == "MEDIA"
    assert present.evidencia == [{"componente": "score_engine", "caminho": "dimensoes.porte_operacionalidade.nivel_potencial", "fingerprint": "x", "nivel_efetivo": "Critico", "nivel_potencial": "Adequado", "nivel_usado": "nivel_potencial"}]


def test_porte_adapter_falls_back_to_legacy_effective_level():
    present = emit_porte({"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Adequado"}}}}, fingerprint="x")[0]

    assert present.valor == "Adequado"
    assert present.evidencia[0]["nivel_usado"] == "nivel"
