from app.services.findings.adapters.porte import emit_porte
from app.services.findings.schemas import Estado


def test_porte_adapter_uses_only_persisted_score_dimension():
    absent = emit_porte({}, fingerprint="x")[0]
    present = emit_porte({"score_engine": {"dimensoes": {"porte_operacionalidade": {"nivel": "Adequado"}}}}, fingerprint="x")[0]
    assert absent.estado == Estado.NAO_VERIFICADO
    assert present.valor == "Adequado"
    assert present.confianca == "MEDIA"
