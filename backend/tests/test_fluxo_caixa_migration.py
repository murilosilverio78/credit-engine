from pathlib import Path


def test_fluxo_caixa_catalog_migration_is_delivered_but_not_applied():
    sql = (Path(__file__).parents[2] / "infra" / "supabase" / "migrations" / "058_achados_fluxo_caixa.sql").read_text(encoding="utf-8")

    for code in (
        "mensalidade_faturada_media", "meses_sem_fatura_12m", "glosa_pct_12m",
        "prazo_ateste_dias", "prazo_liquidacao_dias", "saldo_empenho_ano",
        "empenho_insuficiente", "cobertura_parcela", "execucao_vs_contratado",
    ):
        assert f"'{code}'" in sql
    assert "INSERT INTO finding_catalog" in sql
