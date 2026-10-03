# Auditoria de insumos da politica v0

Inventario por campo das funcoes alcançadas por `consolidar_score` e por
`_fetch`. `Achado` significa um fato append-only da versao atual do emissor;
`Contexto` e coluna de `operations` carregada por `montar_entrada`; e
`Parametro` e valor versionado na migration 045. Nao ha insumo de decisao
marcado como `NADA`.

| Funcao oficial | Campo lido | Origem | Como a politica recebe |
|---|---|---|---|
| `score_saude_cadastral`, `_idade_score` | `data_abertura`/`abertura` | `brasil_api`, `pessoa_juridica` | Achado `idade_empresa_anos` |
| `score_saude_cadastral`, `_capital_score` | `capital_social` | `brasil_api`, `pessoa_juridica` | Achado `capital_social_rs` |
| `score_saude_cadastral`, `_porte_score` | `porte`, `natureza_juridica` | `brasil_api`, `pessoa_juridica` | Achados `porte_cadastral`, `natureza_juridica_empresarial` |
| `score_saude_cadastral`, `_qsa_estabilidade_score` | `qsa[].data_entrada`/`data_inicio`, qualificacao | `brasil_api`, `pessoa_juridica` | Achado derivado sem PII `qsa_estabilidade` |
| `score_saude_cadastral`, `_atividade_restrita` | atividade principal, CNAE e secundarias | `brasil_api`, `pessoa_juridica` | Achado `atividade_restrita` |
| `gates_deterministicos`, `_is_active_record` | situacao cadastral, registros e vigencia | cadastro, CEIS/CNEP/CEPIM/CEAF, acordos | Achados `cadastro_inativo`, `sancao_ativa`, `acordo_leniencia_ativo` |
| `score_relacionamento`, `_active_contracts`, `_contract_duration_years` | quantidades, detalhes, orgaos, situacao, inicio/fim | `contratos`, adicional Comprasnet verificado | Achados de contratos, orgaos, maturidade e Comprasnet |
| `score_relacionamento` | `valor_total_ativo` | `contratos`, ja mesclado ao Comprasnet | Achado `contratos_valor_total_ativo_rs` |
| `score_relacionamento` | HHI, meses e historico | `recursos_recebidos` | Achados `hhi_recebimentos`, `meses_com_recebimento`, `anos_completos_receita` |
| `_ajuste_pd_volatilidade` | CV, anos completos e chaves da serie | `recursos_recebidos` | Achados `volatilidade_cv`, `receita_serie_anual` |
| `score_porte_llm`, `_faturamento_context`, `_cobertura_exposicao` | nivel, flags, faturamento; valor enquadrado | resultado LLM/snapshots e `operations` | Achado `capacidade_operacional`; `valor_enquadrado` em Contexto |
| `score_reputacao`, `_valid_level` | nivel e flags de pesquisa | `web_research`/resultado LLM | Achado `reputacao_mercado` |
| `score_regularidade`, `_certidao_estado` | validade, positiva/negativa e status | tres componentes de certidao | Achados `certidao_*_pendente` |
| `_apply_missing_balance_penalty`, `_document_types` | tipos recursivos `tipo`, `tipo_documento`, `document_type` | `contrato_extracao` | Achado `balanco_catalogado_broadfactor`, fonte `extracao` |
| `_apply_missing_balance_penalty`, `_document_types` | `documents[].document_type` | tabela `documents` da operacao | Mesmo achado, fonte `documentos_operacao`; tambem entra na hash |
| `_add_quote_catalog_documents`, `_document_types` | `cotacoes_broadfactor.tipos_documento` | catalogo da cotacao | Mesmo achado, fonte `cotacao`; `catalog_only_types` so evita duplicacao na montagem do score |
| `_apply_missing_balance_penalty` | tipos de balanco, penalidade, peso de porte | constante e precificacao | Tipo em constante compartilhada; penalidade/pesos em Parametros; teto `min(penalidade, score_porte_potencial * peso_porte)` |
| `_limite_aprovado` | enquadrado, solicitado, margem, saldos, percentual maximo | `operations` | Contexto: `valor_enquadrado`, `valor_solicitado`, `margem_disponivel`, `contrato_saldo`, `saldo_vincendo`, `pct_max_contrato` |
| `_limite_aprovado` | percentual margem/saldo | `PCT_MARGEM_SOBRE_SALDO` | Parametro `pct_margem_sobre_saldo` (0,70) |
| `consolidar_score` | pesos, subpesos, faixas, niveis, haircuts, penalidades, matriz PD | constantes e `get_pricing_config` | Parametros e faixas versionados na 045 |

`catalog_only_types` nao e um quarto fato: e a protecao contra duplicar tipos
ja vindos de `contrato_extracao`. O emissor registra a proveniencia completa e
usa a mesma uniao para decidir a presenca do balanco.

## Invariantes de paridade

- `idade_empresa_anos` preserva a precisao integral de dias/365,25 e carrega
  a data de referencia na evidencia; a faixa e aplicada pelo interpretador.
- Um registro restritivo confirmado (inclusive uma contagem positiva sem
  lista de registros) continua sendo veto quando outra fonte falha. A
  conclusao negativa de sancoes, por outro lado, exige todas as fontes que o
  motor oficial verifica (`pessoa_juridica`, CEIS, CNEP, CEPIM e acordos).
- A presenca de balanco depende somente das tres fontes documentais da tabela
  acima. Indisponibilidade de sancoes nao transforma esse fato conhecido em
  `NAO_VERIFICADO`.
- `reputacao_mercado` recebe o nivel efetivo de `score_reputacao`: nivel
  invalido volta a `Adequado` e `Excepcional` sem sinal positivo e limitado a
  `Forte`; a evidencia preserva as flags da decisao.
- Para relacionamento, contagens ausentes usam os mesmos fallbacks do motor
  oficial (detalhes de contratos e orgaos ativos), e mes de recebimento
  indisponivel nao e interpretado como zero.
- Capital social zero tem a mesma semantica de ausente do motor oficial e
  aciona o teto de dado material da saude cadastral.
