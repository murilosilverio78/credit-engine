import ast
import inspect
from datetime import date

from app.services.policy.engine import avaliar
from app.services.policy.types import EntradaPolitica, FindingValue


def _params():
    return {"nivel_nota":{"Excepcional":95,"Forte":85,"Adequado":70,"Atencao":55,"Fraco":40,"Critico":20},"pesos_merito":{"relacionamento_governamental":.3,"porte_operacionalidade":.28,"saude_cadastral":.24,"reputacao_mercado":.18},"subpesos_cadastral":{"idade":.35,"capital":.29,"porte":.24,"estabilidade":.12},"subpesos_relacionamento":{"volume":.3,"concentracao":.3,"historico":.25,"maturidade":.15},"faixas_idade":[{"ate":1,"nota":25},{"ate":2,"nota":45},{"ate":5,"nota":60},{"ate":10,"nota":75},{"ate":20,"nota":88},{"nota":95}],"faixas_capital":[{"ate":10000,"nota":30},{"ate":50000,"nota":50},{"ate":200000,"nota":65},{"ate":500000,"nota":78},{"ate":2000000,"nota":88},{"nota":95}],"faixas_porte_cadastral":{"EPP":72,"natureza_nao_empresarial":55,"fallback":58},"faixas_estabilidade_qsa":{"sem_dados":75,"maior_3":85,"de_1_a_3":70,"menor_1":55},"hhi_min_meses_recebimento":6,"piso_fator_regularidade":.75,"haircut_certidao_por_estado":{"ausente":0,"negativa":0},"penalidade_cnd_federal_ausente":6,"penalidade_cndt_ausente":6,"penalidade_fgts_ausente":6,"penalidade_balanco_ausente":10,"faixas_rating":{"A":85,"B":70,"C":55,"D":40,"E":0},"pd_min_anos_completos_volatilidade":2,"pd_cv_corte_moderado":.7,"pd_cv_corte_alto":.8,"pd_mult_historico_insuficiente":1.08,"pd_mult_volatilidade_moderada":1.08,"pd_mult_volatilidade_alta":1.15,"pd_performada":.016,"pd_mult_por_rating":{"A":.6,"B":1,"C":2,"D":5,"E":10}}


def test_engine_is_pure_and_tracks_facts():
    source = inspect.getsource(__import__("app.services.policy.engine", fromlist=["x"]))
    assert "score_engine" not in source and "supabase" not in source
    findings = {code: FindingValue(code, value, "CONFIRMADO", "ALTA", "run") for code, value in {"idade_empresa_anos":4,"capital_social_rs":300000,"porte_cadastral":"EPP","qsa_estabilidade":{"entrada_mais_recente":"2020-01-01"},"contratos_ativos_qtd":3,"contratos_total_qtd":3,"orgaos_distintos_qtd":2,"hhi_recebimentos":3000,"meses_com_recebimento":8,"maturidade_max_anos":4,"capacidade_operacional":"Atencao","reputacao_mercado":"Atencao","certidao_cnd_federal_pendente":{"estado":"ausente"},"certidao_cndt_pendente":{"estado":"ausente"},"certidao_fgts_pendente":{"estado":"ausente"},"balanco_ausente":False}.items()}
    result = avaliar(EntradaPolitica({"valor_enquadrado":100,"valor_solicitado":120}, findings), _params(), [], date(2026, 1, 1))
    assert result.score >= 0 and result.limite_aprovado_rs == 100
    assert result.trilha["regularidade"]
