# Auditoria de insumos da politica v0

| Saida oficial | Funcoes e insumos | Destino da politica |
|---|---|---|
| Saude cadastral | `score_saude_cadastral`: Brasil/PJ (`situacao`, abertura, capital, porte, natureza, QSA, atividade) | achados de cadastro, capital, porte, natureza, QSA e atividade; situacao e veto |
| Relacionamento | `score_relacionamento`: contratos ativos/totais, orgaos, detalhes, HHI, meses, Comprasnet | achados de contratos, orgaos, maturidade, HHI, meses e Comprasnet |
| Niveis LLM | `score_porte_llm`, `score_reputacao`, `_valid_level` | `capacidade_operacional`, `reputacao_mercado` |
| Regularidade | `score_regularidade`, `_certidao_estado`, `_apply_missing_balance_penalty` | achados de tres certidoes, balanco e catalogo |
| Vetos | `gates_deterministicos`, `_is_active_record` | achados `cadastro_inativo`, `sancao_ativa`, `acordo_leniencia_ativo` |
| PD | `_ajuste_pd_volatilidade`: CV, anos informados, serie anual, parametros e matriz | `volatilidade_cv`, `anos_completos_receita`, `receita_serie_anual`; parametros 045 |
| Limite | `_limite_aprovado`: enquadrado, solicitado, margem, saldo, contrato, total ativo e percentual | contexto de operation; `contratos_valor_total_ativo_rs`; `pct_margem_sobre_saldo` |

Constantes de pesos, niveis, faixas, haircuts, penalidades e risco sao parametros da
045. `PCT_MARGEM_SOBRE_SALDO` e `0.70` em `eligibility_service.py` e passa a ser
`pct_margem_sobre_saldo`. Nenhum insumo de decisao acima fica sem destino.
