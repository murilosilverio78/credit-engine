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
| D4 | Valor acima do ticket máximo ganha "Reenquadrar em R$ X", com X = min(valor enquadrável, ticket_maximo) |
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
| `acima_ticket_maximo` | COMERCIAL | Valor acima do ticket máximo | pedido, saldo vincendo, enquadrável, ticket máximo, proposta possível | Reenquadrar em R$ X (D4), Arquivar |
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

Botão "Enviar para análise completa": desabilitado com veto; com reprovação de crédito, pede justificativa, registra na `audit_trail` e dispara a análise paga como o "Gerar relatório" de hoje.

## 6. Contrato de dados

### 6.1 Motivo estruturado

Nova coluna `cotacoes_broadfactor.estagio_motivos jsonb` (migration 076), gravada junto com `estagio_motivo` (o texto continua sendo gravado, por compatibilidade):

```json
[
  {"codigo": "SANCAO_CEIS", "codigo_origem": "Sancao ativa identificada em bases restritivas",
   "classe": "VETO", "fonte": "ceis", "ref": {"component": "ceis", "registro_id": 388292}},
  {"codigo": "CAPACIDADE_INSUFICIENTE", "codigo_origem": "capacidade_insuficiente:0",
   "classe": "CREDITO", "fonte": "contratos_pncp", "ref": {"divergencia_fonte": true}}
]
```

Backfill da coluna a partir de `estagio_motivo` para as cotações existentes.

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

`score_engine._is_active_record` procura `situacao`/`status`/`situacao_cadastral`/`data_fim`, mas o CEIS do Portal usa `dataFimSancao` (dd/mm/aaaa) e `tipoSancao`. Por isso o motivo "Sancao ativa em CEIS" nunca é gerado e o veto depende só do resumo de `pessoa_juridica`. Corrigir para considerar ativo o registro com `dataFimSancao` vazia ou maior ou igual à data atual, e incluir teste com o registro real da ORBENK.

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
| 1 | Migration 076, motivo estruturado + backfill, correção do `_is_active_record`, endpoint de resumo | JSON dos 5 casos em produção |
| 2 | Tela da cotação, rota a partir das abas, fim do polling em parada | Tela em produção × protótipo, 5 casos |
| 3 | Endpoints de ação e botões, com auditoria | Ação simulada em cotação de TESTE |
