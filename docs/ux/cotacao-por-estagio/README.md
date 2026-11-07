# Tela da cotação por estágio ("onde parou e por quê")

Status: aprovado por Murilo em 06/11/2026.
Protótipo navegável: https://claude.ai/artifact/CC1PipPbZnjhZKSS8SaJo1
Fonte do protótipo: [`prototipo.dc.html`](./prototipo.dc.html). O método `dados()` do script tem os 5 casos completos e serve de referência para textos, classes, evidências e ações. A marcação (`<x-dc>`) mostra a hierarquia visual e os estilos.

## 1. Problema

Hoje a tela de detalhe é da operação (`/operations/{id}`). Fora da aba Qualificadas:

- cotações de "Não enquadradas" e "Sem documentos" não têm operação, então não têm tela de detalhe;
- cotações paradas mostram o pipeline com cronômetro correndo e componentes em "aguarda" que nunca vão rodar;
- não existe resumo de onde a cotação parou nem por quê; o motivo fica em `cotacoes_broadfactor.estagio_motivo` como texto livre;
- a linha de uma consulta com achado (ex.: CEIS com sanção) aparece como "✓ ok" verde, porque o status mostra a coleta e não o resultado;
- o selo `reprovada_triagem` é verde.

Caso real: ORBENK, cotação C-1793047365028, operação aa8d8ae1. Reprovada por sanção no CEIS e capacidade zerada, sem nenhuma das duas visível na tela.

## 2. Decisões (Murilo, 06/11/2026)

| # | Decisão |
|---|---|
| D1 | Uma tela única por **cotação**, com esqueleto fixo e conteúdo por estágio (não uma tela por aba) |
| D2 | Reprovação de crédito que **não é veto** ganha o botão "Enviar para análise completa", liberado para **qualquer alçada** (analista, gerente, diretor), com justificativa obrigatória (mínimo 10 caracteres) e registro na trilha de auditoria. Com veto presente, o botão aparece desabilitado com o texto "Bloqueado: existe veto ativo" |
| D3 | Cotação parada por falta de documentos ganha o botão "Rodar consultas gratuitas mesmo assim" |
| D4 | (revisada em 07/11/2026) A triagem não reprova mais por valor acima do ticket máximo: limita o valor enquadrado ao ticket máximo e a cotação segue no funil. O botão "Reenquadrar" deixa de existir |
| D6 | (07/11/2026) Exceção ao teto: no diálogo de relatório, só a alçada **diretor** pode analisar acima do ticket máximo, até o enquadrável pelo contrato (pct_max_contrato × saldo vincendo), com justificativa obrigatória, auditoria e selo "Acima do ticket máximo da política" no relatório e na proposta. Limite e taxa continuam determinísticos |
| D5 | Sanção ativa continua veto sem distinção de esfera (decisão de 16/10/2026); a tela só mostra a abrangência como informação |

## 3. Esqueleto da tela (de cima para baixo)

1. **Voltar** para a aba de origem.
2. **Cabeçalho**: cotacao_id, CNPJ, razão social, contrato/UASG quando houver, selo de estado, 3 KPIs (valor pedido, valor enquadrado ou capacidade, expiração com dias restantes).
3. **Trilha da jornada**: 5 passos (Não enquadradas → Sem documentos → Em verificação → Qualificadas → Relatório). Passos já superados em verde, o passo atual na cor da classe com "parou aqui" (ou "aqui agora" em Qualificadas), seguintes em cinza. Encerrada: o último estágio alcançado em cinza com "último estágio".
4. **Card "Onde parou e por quê"**, com fundo na cor da classe: selo da classe, "Parou em X em dd/mm/aaaa hh:mm", título em linguagem de comitê, resumo de 1 a 3 frases e a lista de motivos. Cada motivo tem chip, título e "ver evidência"; ao abrir, mostra texto, grade de evidências (chave/valor, valor ruim em vermelho, valor bom em verde) e a linha de fonte. O primeiro motivo começa aberto.
5. **"O que fazer agora"**: botões da seção 5 com uma nota curta embaixo de cada um.
6. **Consultas executadas** (recolhido por padrão): tabela Consulta / Coleta / Resultado, com o resultado em chip (verde "nada consta", vermelho achado restritivo, amarelo dado a revisar, cinza informativo). Abaixo, um bloco cinza dizendo o que **não** foi executado e por quê. Antes de Em verificação, o título vira "Regras aplicadas (sem custo)" e lista as regras da Broadfactor.

Regras gerais:
- nenhum cronômetro, contador ou polling quando a cotação está parada ou encerrada;
- nenhum componente com "aguarda" se ele não vai rodar;
- selo `reprovada_triagem` nunca verde;
- funciona em largura de celular (cards empilham, tabela rola na horizontal).

## 4. Classes de parada

| Classe (código) | Selo | Cor | Quando |
|---|---|---|---|
| `VETO` | Definitiva | vermelho (#B42318 / #FEF3F2) | sanção, situação cadastral diferente de ATIVA, tipo não elegível, abaixo do ticket mínimo, janela de expiração insuficiente |
| `CREDITO` | Reprovada (reversível com alçada) | vermelho | capacidade, prazo vincendo, histórico, órgãos pagadores, contrato não encontrado |
| `REVISAR_DADO` | Revisar dado | amarelo (#B54708 / #FFFAEB) | indisponibilidade de fonte, cadastro não verificado, divergência entre fontes |
| `CLIENTE` | Depende do cliente | azul (#175CD3 / #EFF8FF) | sem documentos |
| `COMERCIAL` | Reversível pelo comercial | azul | acima do ticket máximo |
| `EM_ANDAMENTO` | Em andamento | verde (#067647 / #ECFDF3) | qualificada, sem motivo |
| `ENCERRADA` | Encerrada | cinza (#344054 / #F2F4F7) | expirada, ausente na listagem |

Classe da cotação = a mais grave dos motivos, nesta ordem: ENCERRADA (sempre prevalece) > VETO > CREDITO > REVISAR_DADO > CLIENTE > COMERCIAL > EM_ANDAMENTO.

## 5. Catálogo de motivos

Os códigos de hoje continuam válidos. O que muda é que cada motivo passa a ser gravado estruturado (seção 6) e traduzido num único lugar do backend.

| Código atual | Classe | Título na tela | Evidência | Ações |
|---|---|---|---|---|
| `tipo_nao_elegivel:<tipo>` | VETO | Tipo de recebível não elegível | tipo recebido | Arquivar |
| `abaixo_ticket_minimo` | VETO | Valor abaixo do ticket mínimo | valor pedido, enquadrável, ticket mínimo | Arquivar |
| `acima_ticket_maximo` (legado; deixa de ser produzido pela D4) | COMERCIAL | Valor acima do ticket máximo | pedido, saldo vincendo, enquadrável, ticket máximo, proposta possível | Arquivar |
| `janela_expiracao_insuficiente` | VETO | Cotação expira antes do prazo mínimo | expiração, dias restantes, mínimo | nenhuma |
| `sem_documentos_broadfactor` | CLIENTE | Nenhum documento na Broadfactor | checklist dos documentos esperados (recebido / não recebido) | Pedir documentos ao cliente (mensagem pronta), Rodar consultas gratuitas mesmo assim (D3) |
| `Sancao ativa ...` (gate de sanções, CEIS/CNEP/CEPIM/CEAF/leniência) | VETO | Sanção ativa no <BASE> | órgão sancionador e esfera, tipo, fundamento, vigência com dias restantes, abrangência, processo e publicação | Copiar mensagem de recusa, Lembrar na data de fim da sanção |
| `Situacao cadastral diferente de ATIVA` | VETO | Situação cadastral <X> | situação e data | Copiar mensagem de recusa |
| `capacidade_insuficiente:<v>` | CREDITO (+ REVISAR_DADO se houver divergência de fonte) | Capacidade do contrato insuficiente | vigência PNCP × Comprasnet, prazo vincendo, valor global por fonte, mensal, capacidade calculada, divergência | Recoletar contrato, Enviar para análise completa (D2) |
| `prazo_vincendo_insuficiente:<n>d` | CREDITO | Prazo vincendo abaixo de 60 dias | prazo, fim da vigência, fonte | Recoletar contrato, Enviar para análise completa |
| `historico_recebimentos_insuficiente:<n>m` | CREDITO | Histórico de recebimentos curto | meses com recebimento, mínimo | Enviar para análise completa |
| `orgaos_pagadores_insuficientes:<n>` | CREDITO | Poucos órgãos pagadores | n órgãos, mínimo | Enviar para análise completa |
| `contrato_comprasnet_nao_encontrado` | CREDITO | Contrato não localizado no Comprasnet | contrato, UASG | Recoletar contrato, Enviar para análise completa |
| `indisponibilidade_fonte:<comp>`, `situacao_cadastral_nao_verificada` | REVISAR_DADO | Consulta <fonte> indisponível | componente, último erro, horário | Recoletar |
| `cotacao_expirada` | ENCERRADA | Cotação expirada | último estágio alcançado, datas | Abrir análise (se houver operação) |
| `cotacao_ausente_na_listagem` | ENCERRADA | Cotação saiu da listagem da Broadfactor | último estágio alcançado, datas, nº de documentos | Abrir análise, Avisar comercial |
| `QUOTATION_INACTIVE` | ENCERRADA | Cotação inativa na Broadfactor | último estágio alcançado, datas | Abrir análise (se houver operação) |
| `arquivada_manual` (novo, fatia 3) | ENCERRADA | Arquivada por <usuário> | autor, data, justificativa | nenhuma |
| `cedente_nao_pj:<tipo>` | VETO | Cedente não é pessoa jurídica | tipo recebido | Arquivar |
| `erro_documentos_broadfactor:<erro>` | REVISAR_DADO | Falha ao consultar documentos na Broadfactor | horário; o texto da exceção só no detalhe técnico | Recoletar |
| `cobertura_insuficiente:<r>` (legado, não é mais produzido) | CREDITO | Cobertura de recebimentos insuficiente | razão, mínimo | Enviar para análise completa |

Botão "Enviar para análise completa": desabilitado com veto; com reprovação de crédito, pede justificativa, registra na `audit_trail` e dispara a análise paga como o "Gerar relatório" de hoje.

## 6. Contrato de dados

### 6.1 Motivo estruturado (revisado após o diagnóstico da fatia 0)

Sem coluna nova e sem backfill. Um único tradutor no backend (`backend/app/services/cotacao_resumo/motivos.py`) recebe o `estagio_motivo` atual (incluindo os textos dos gates de sanção e cadastro) e os snapshots da operação, e devolve a lista estruturada na leitura:

```json
[
  {"codigo": "SANCAO_CEIS", "codigo_origem": "Sancao ativa identificada em bases restritivas",
   "classe": "VETO", "fonte": "ceis", "ref": {"component": "ceis", "registro_id": 388292}},
  {"codigo": "CAPACIDADE_INSUFICIENTE", "codigo_origem": "capacidade_insuficiente:0",
   "classe": "CREDITO", "fonte": "contratos_pncp", "ref": {"divergencia_fonte": true}}
]
```

Código não reconhecido vira classe REVISAR_DADO com o texto original, e o tradutor registra log de aviso. A divergência PNCP × Comprasnet é calculada na leitura, comparando `contrato_cedido.valor_global/data_inicio_vigencia/data_fim_vigencia` (PNCP) com `contrato_comprasnet.valor_global/vigencia_inicio/vigencia_fim`.

### 6.2 Endpoint `GET /api/v1/cotacoes/{cotacao_id}/resumo`

Devolve tudo o que a tela desenha, já traduzido. A tela não interpreta código nenhum.

```json
{
  "cotacao_id": "C-1793047365028", "operation_id": "aa8d8ae1-...", "cnpj": "79283065000141",
  "razao_social": "...", "contrato": "Contrato 00037/2025 · UASG 158493",
  "aba_origem": "REPROVADAS",
  "selo": {"texto": "Reprovada na triagem", "classe": "VETO"},
  "kpis": [{"rotulo": "Valor pedido", "valor": 100000, "formato": "BRL", "nota": null}],
  "trilha": {"estagio_atual": "DOCUMENTADA", "estagio_max": "DOCUMENTADA", "encerrada": false},
  "parada": {"classe": "VETO", "parou_em": "2026-11-05T16:38:21Z", "titulo": "...", "resumo": "..."},
  "motivos": [{"codigo": "SANCAO_CEIS", "classe": "VETO", "chip": "Veto", "titulo": "...", "texto": "...",
               "evidencias": [{"rotulo": "Vigência", "valor": "06/05/2026 a 05/05/2027 · 180 dias restantes", "tom": "ruim"}],
               "fonte": "Portal da Transparência (CEIS), consulta de 05/11/2026"}],
  "acoes": [{"id": "enviar_analise_completa", "rotulo": "...", "nota": "...", "habilitada": false,
             "motivo_bloqueio": "Bloqueado: existe veto ativo", "exige_justificativa": true}],
  "consultas": {"titulo": "Consultas executadas", "itens": [{"nome": "CEIS", "coleta": "ok", "resultado": "1 sanção ativa", "tom": "ruim"}],
                "nao_executados": "..."}
}
```

`tom` em evidências e resultados: `bom` | `ruim` | `atencao` | `neutro`. Dias restantes são calculados na data da consulta à API, não na data da coleta.

### 6.3 Ações (POST)

- `POST /cotacoes/{id}/reenquadrar` (D4)
- `POST /cotacoes/{id}/consultas-gratuitas` (D3)
- `POST /cotacoes/{id}/analise-completa` com `{justificativa}` (D2); 409 se houver veto
- `POST /cotacoes/{id}/arquivar`
- Recoletar: reaproveita `POST /operations/{id}/recoletar`
- "Copiar mensagem" e "Pedir documentos": só front, texto vindo do endpoint de resumo

Todas registram em `audit_trail` com autor e alçada.

## 7. Correção obrigatória junto (bug)

`score_engine._is_active_record` procura `situacao`/`status`/`situacao_cadastral`/`data_fim`, mas o CEIS do Portal usa `dataFimSancao` (dd/mm/aaaa) e `tipoSancao`. Por isso o motivo "Sancao ativa em CEIS" nunca é gerado e o veto depende só do resumo de `pessoa_juridica`.

Regra nova, nesta ordem:
1. token explícito de inativo (`inativo`, `baixado`, `encerrado`, `cancelado`, `suspenso`) → inativo;
2. `dataFimSancao` (ou `data_fim`) parseável: ativo se maior ou igual a hoje, inativo se menor;
3. data ausente ou inválida → regra de tokens atual.

A mesma função serve o gate, o score e o adaptador de achados (`cadastro_regularidade.py`). Testes: data passada, hoje, futura, inválida, ausente, token inativo com data futura, e o registro real da ORBENK (fim 05/05/2027).

Impacto medido em produção (06/11/2026): 29 registros CEIS vigentes em 10 operações e 2 vencidos, ambos da CARE SURGICAL (2 operações). Ver a regra de precedência do resumo `pessoa_juridica` na seção 7.1.

### 7.1 Resumo `pessoa_juridica` × registros detalhados

O gate "Sancao ativa identificada em bases restritivas" usa o booleano `possui_sancao` do resumo, que fica `true` mesmo quando todos os registros detalhados estão vencidos (caso CARE SURGICAL). Regra: quando CEIS, CNEP e CEPIM foram consultados com sucesso, os registros detalhados prevalecem e o resumo não gera veto sozinho; o resumo só vale como veto quando alguma dessas bases está indisponível. (Pendente de confirmação do Murilo.)

## 8. Critérios de aceite (casos reais de produção)

| Caso | Aba | Deve mostrar |
|---|---|---|
| APOLLO, C-1793791899193 | Não enquadradas | classe COMERCIAL; 1 motivo "Valor acima do ticket máximo" com enquadrável R$ 1.626.770,44 e proposta possível R$ 700.000,00; botões "Reenquadrar em R$ 700.000" e "Arquivar"; bloco "Regras aplicadas (sem custo)" |
| JJX, C-1793887270802 | Sem documentos | classe CLIENTE; checklist de documentos todos "não recebido"; botões "Pedir documentos ao cliente" e "Rodar consultas gratuitas mesmo assim" |
| ORBENK, C-1793047365028 | Reprovadas | classe VETO; selo vermelho; 2 motivos (sanção CEIS aberta com Porto Alegre, Lei 14.133 art. 156 III, fim 05/05/2027; capacidade com PNCP 15/09/2026 × Comprasnet 10 meses); "Enviar para análise completa" desabilitado com "Bloqueado: existe veto ativo"; CEIS na tabela com resultado "1 sanção ativa" em vermelho; CNEP/CEPIM/leniência "nada consta"; nenhum cronômetro |
| PROCLIMA, C-1793367917902 | Qualificadas | classe EM_ANDAMENTO; sem motivos; botão "Gerar relatório" |
| GREEN HOUSE, C-1793382852614 | Encerradas | classe ENCERRADA; trilha com Qualificadas como último estágio; somente "Abrir análise" e "Avisar comercial" |

Gerais: abrir qualquer linha de qualquer aba leva à tela da cotação; nenhuma tela parada tem polling (verificar na aba de rede); testes Playwright cobrindo os 5 casos com fixtures.

## 9. Entrega em fatias

| Fatia | Escopo | Valida |
|---|---|---|
| 0 | Diagnóstico somente leitura | Claude revisa e Murilo aprova o plano |
| 1 | Correção do `_is_active_record` e da precedência 7.1, tradutor de motivos, `GET /cotacoes/{id}/resumo` (somente leitura, sem migration) | JSON dos 5 casos em produção |
| 2 | Tela `/cotacoes/[cotacao_id]`, todas as linhas das abas abrindo nela, `/operations/[id]` sem cronômetro nem polling em estados não vivos, Playwright com mock de API (`page.route`) e `data-testid` | Tela em produção × protótipo, 5 casos |
| 3 | Migration 076 (enum `quote_reframed`, `quote_free_checks_requested`, `quote_full_analysis_requested`, `quote_archived`), endpoints de ação com alçada e auditoria, recalculador de capacidade exposto por fronteira autenticada, análise completa aceitando reprovação CREDITO com justificativa, arquivar como ENCERRADA/`arquivada_manual` | Ação em cotação de TESTE |
