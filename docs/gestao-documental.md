# Especificação Final - Módulo de Gestão Documental

AntecipaGov Credit Engine

Versão 1.0

Data: 07/09/2026

Status: pronta para desenvolvimento

## 1. Resumo executivo

O módulo de gestão documental deve obter, armazenar, validar e reaproveitar
documentos de empresas e pessoas vinculadas às operações de crédito. Seu
objetivo é transformar documentação em evidência estruturada para o score e
para o envio de propostas vinculantes, sem tornar a intervenção humana parte
normal da esteira.

A análise de crédito e o envio da proposta são eventos separados. A ausência
de documentos não impede o cálculo do mérito, mas documentos opcionais ausentes
podem penalizar o score. O envio à Broadfactor é bloqueado enquanto os
documentos obrigatórios não estiverem satisfeitos.

O Credit Engine é responsável por validar os poderes de representação. A
assinatura da nota comercial é operacionalizada pelo time da Broadfactor. O
Credit Engine deve identificar arranjos de assinatura juridicamente possíveis
e liberar o envio quando pelo menos um arranjo completo tiver poderes e
identidades validados. A Broadfactor deverá utilizar um dos arranjos aprovados
na formalização.

## 2. Problema

Hoje o sistema não possui uma fonte canônica para documentos, não reaproveita
evidências entre operações do mesmo titular e não distingue presença de
validade. Certidões e balanço ausentes geram 28 pontos de penalização, mas a
chegada posterior de um arquivo não produz, de forma confiável, validação,
materialização e reprocessamento.

O upload atual é específico para certidões, e o catálogo da Broadfactor
colapsa respostas vazias, proibidas e falhas no mesmo resultado. Além disso,
um POST de proposta à Broadfactor é irreversível, o que exige consistência
transacional, idempotência e reconciliação de respostas ambíguas.

## 3. Objetivos

1. Reaproveitar automaticamente documentos válidos entre operações do mesmo
   titular.
2. Diferenciar documento recebido, documento válido e requisito satisfeito.
3. Materializar somente evidências válidas no score e retirar essa evidência
   quando ela vencer ou for invalidada.
4. Automatizar os casos societários simples e encaminhar somente exceções para
   revisão humana.
5. Impedir envio de proposta sem documentação obrigatória e sem decisão de
   crédito compatível com o score corrente.
6. Sobreviver a reinício de container sem perder jobs nem duplicar consultas,
   validações ou propostas.
7. Manter trilha auditável da origem, validação, política, score, decisão e
   envio usados em cada proposta.

## 4. Metas de sucesso

Metas iniciais para os primeiros 60 dias após ativação do gate:

| Métrica | Meta |
|---|---:|
| Operações com documentação resolvida sem ação interna | >= 80% |
| Reaproveitamento em nova operação de titular conhecido | >= 90% |
| Documentos processados automaticamente, excluídos ilegíveis | >= 90% |
| Operações encaminhadas para exceção humana | <= 20% |
| Jobs órfãos sem recuperação automática | 0 |
| Propostas duplicadas por retry | 0 |
| Envios sem score e documentação na mesma revisão | 0 |
| Tempo p95 entre documento recebido e estado recomputado | <= 10 min |

As metas são hipóteses operacionais. Devem ser revisadas com dados reais, sem
relaxar os objetivos de zero duplicidade e zero envio inconsistente.

## 5. Decisões de produto

### 5.1 Proposta vinculante

O POST à Broadfactor é vinculante e irreversível. Não existe envio exploratório
para apenas mostrar uma taxa ao cliente.

### 5.2 Crédito e proposta são estados independentes

```text
decisao_credito:
  PENDENTE | APROVADA | REJEITADA

proposta:
  NAO_PREPARADA | BLOQUEADA | PRONTA | ENVIANDO |
  ENVIADA | EXPIRADA | FALHA_ENVIO | ENVIO_INDETERMINADO
```

A documentação não bloqueia a análise nem a decisão de mérito. O gate
documental protege o envio.

`BLOQUEADA` usa uma lista de motivos, porque crédito pendente, documentação,
score obsoleto e termos expirados podem coexistir. Motivos iniciais:

```text
CREDIT_DECISION_PENDING
DOCUMENTS_PENDING
SCORE_STALE
TERMS_INVALID
BACKGROUND_JOB_RUNNING
```

### 5.3 Efeito de novo score sobre decisão já aprovada

Toda decisão de crédito fica vinculada à versão do score, à revisão documental
e ao envelope aprovado.

A aprovação permanece válida automaticamente quando, após reprocessamento:

- rating não piora;
- PD e LGD não aumentam;
- valor enquadrado não supera o valor aprovado;
- prazo não supera o prazo aprovado;
- taxa e demais termos permanecem dentro do envelope aprovado;
- a alçada necessária não aumenta.

Se qualquer condição piorar, `decisao_credito` volta para `PENDENTE`, com motivo
`SCORE_OU_TERMOS_ALTERADOS`, e a proposta fica `BLOQUEADA`, com motivo
`CREDIT_DECISION_PENDING`.

### 5.4 Waiver

Somente o conjunto societário pode receber waiver nesta versão. O waiver:

- exige role `diretor`;
- vale para uma única operação;
- exige justificativa;
- possui validade;
- é revogado por alteração material da operação;
- não é reaproveitado em outra operação.

Identidade de pessoa necessária em um arranjo de assinatura não pode ser
dispensada.

### 5.5 Reaproveitamento por titular

- instrumentos societários são reaproveitáveis por raiz de CNPJ;
- certidões e documentos financeiros são reaproveitados somente quando a
  abrangência nominal extraída do próprio documento cobre o CNPJ da operação;
- documentos pessoais são reaproveitados pelo CPF;
- documentos específicos de contrato ou cotação permanecem vinculados à
  operação correspondente.

### 5.6 Pessoa física cedente

Cedente pessoa física está fora do escopo. Cotações cujo fornecedor seja CPF
continuam descartadas na ingestão com motivo explícito. Pessoas físicas são
suportadas apenas como representantes, signatários ou avalistas de pessoa
jurídica.

## 6. Não objetivos desta versão

1. Assinar a nota comercial dentro do Credit Engine.
2. Substituir a análise jurídica de estruturas societárias complexas.
3. Aceitar cedente pessoa física.
4. Bonificar o score apenas pela existência de IR de sócio ou avalista.
5. Penalizar divergência de faturamento antes de existir amostra calibrada.
6. Armazenar documentos em `component_snapshots` ou base64.
7. Reprocessar consultas externas pagas quando apenas o score precisar mudar.

## 7. Conceitos e separação de responsabilidades

### 7.1 Artefato

Arquivo imutável recebido da Broadfactor ou por upload. O artefato não é
válido apenas por existir.

### 7.2 Extração

Dados obtidos do conteúdo por parser, OCR ou LLM. A extração descreve o que foi
encontrado, mas não decide se atende à política.

### 7.3 Validação

Resultado versionado da aplicação de regras sobre um artefato e sua extração.

### 7.4 Requisito

Obrigação documental de uma operação. Pode exigir uma ou várias evidências,
aceitar alternativas ou ser dispensado.

### 7.5 Materialização

Projeção determinística dos requisitos satisfeitos para consumo do score e das
interfaces legadas. A fonte de verdade permanece nos requisitos e validações.

### 7.6 Arranjo de assinatura

Conjunto de uma ou mais pessoas que, em conjunto, possui poderes suficientes
para assinar a nota comercial no valor da operação.

## 8. Modelo de dados

As migrations devem ser reexecutáveis, habilitar RLS, criar policies para
`service_role` em blocos tolerantes a duplicidade e terminar com:

```sql
NOTIFY pgrst, 'reload schema';
```

Nenhuma migration deve executar chamadas externas ou reprocessar a carteira
inteira dentro da transação de DDL.

### 8.1 `document_types`

Catálogo extensível. Não usar enum PostgreSQL, pois os tipos da Broadfactor são
negociáveis e podem mudar.

| Campo | Tipo | Regra |
|---|---|---|
| code | TEXT PK | código estável interno |
| label | TEXT | nome de exibição |
| holder_scope | TEXT | LEGAL_ENTITY, PERSON ou OPERATION |
| validator_code | TEXT | pipeline de validação |
| active | BOOLEAN | default true |
| metadata_schema | JSONB | campos esperados da extração |
| created_at | TIMESTAMPTZ | default now |

Tipos iniciais:

```text
ATO_CONSTITUTIVO_CONSOLIDADO
ALTERACAO_CONTRATUAL
PROCURACAO
ATA_REPRESENTACAO
IDENTIDADE
CND_FEDERAL
CNDT
CRF_FGTS
BALANCO_ULTIMO_EXERCICIO
BALANCO_PENULTIMO_EXERCICIO
BALANCETE_ATUAL
FATURAMENTO_12M
IR_PESSOA_FISICA
```

Tipos desconhecidos da Broadfactor são armazenados como
`BROADFACTOR_DESCONHECIDO`, preservando o código original nos metadados. Eles
não satisfazem requisitos até serem mapeados.

### 8.2 `documents`

Evolução da tabela existente, sem criar uma segunda fonte de verdade.

| Campo | Tipo | Regra |
|---|---|---|
| id | UUID PK | existente |
| operation_id | UUID NULL | apenas quando específico da operação |
| type_code | TEXT FK | substitui gradualmente o enum legado |
| holder_type | TEXT | LEGAL_ENTITY, PERSON ou OPERATION |
| holder_document | VARCHAR(14) | CNPJ ou CPF normalizado |
| holder_root | VARCHAR(8) NULL | raiz quando empresa |
| holder_name | TEXT NULL | nome conhecido |
| source | TEXT | BROADFACTOR ou UPLOAD_MANUAL |
| source_scope | TEXT NULL | cotação ou contexto da origem |
| external_id | TEXT NULL | identificador na origem |
| content_sha256 | CHAR(64) | obrigatório após armazenamento |
| storage_key | VARCHAR(500) | objeto privado no R2 |
| filename | TEXT NULL | nome higienizado |
| mime_type | TEXT | MIME detectado, não declarado |
| file_size_bytes | BIGINT | tamanho real |
| security_status | TEXT | QUARANTINED, CLEAN, INFECTED ou SCAN_ERROR |
| scanned_at | TIMESTAMPTZ NULL | conclusão do scan |
| valid_until | DATE NULL | validade extraída |
| reference_year | INTEGER NULL | exercício contábil |
| period_start | DATE NULL | início da competência |
| period_end | DATE NULL | fim da competência |
| superseded_by | UUID NULL | nova versão do artefato |
| received_by_type | TEXT | user, external_token ou system |
| received_by_id | TEXT NULL | ator ou job |
| created_at | TIMESTAMPTZ | default now |

Restrições e índices:

```text
UNIQUE (holder_type, holder_document, type_code, content_sha256)
UNIQUE parcial (source, source_scope, external_id) WHERE external_id IS NOT NULL
INDEX (holder_document, type_code, created_at DESC)
INDEX (holder_root, type_code, created_at DESC)
INDEX (operation_id)
INDEX (valid_until)
```

`storage_key` é persistida; URL assinada é criada sob demanda e nunca gravada.
O artefato é imutável. `superseded_by` pode ser preenchido posteriormente, mas
o arquivo e seus metadados de origem não são sobrescritos.

### 8.3 `document_source_snapshots`

Registra cada consulta de catálogo, inclusive falhas.

| Campo | Descrição |
|---|---|
| id | UUID |
| operation_id | operação consultada |
| source | BROADFACTOR |
| source_scope | cotacao_id |
| outcome | OK, EMPTY, FORBIDDEN ou ERROR |
| catalog_hash | hash canônico dos metadados ordenados |
| fetched_at | início da consulta |
| completed_at | conclusão |
| error_message | erro sanitizado |
| item_count | quantidade recebida |

`ERROR` e `FORBIDDEN` nunca removem artefatos nem alteram requisito satisfeito
para ausente. `last_successful_refresh_at` é derivado do último `OK` ou `EMPTY`.

### 8.4 `document_extractions`

Tabela append-only.

| Campo | Descrição |
|---|---|
| id | UUID |
| document_id | artefato extraído |
| extractor_type | parser, OCR ou LLM |
| extractor_name | implementação ou modelo |
| extractor_version | versão do parser, prompt ou modelo |
| result | JSONB estruturado |
| evidence | páginas, trechos e coordenadas quando disponíveis |
| quality_flags | lista de alertas |
| created_at | data da extração |

Texto integral não deve ser persistido em logs. Se for necessário armazenar o
texto extraído, ele permanece em objeto privado separado, sujeito à mesma
retenção do documento.

### 8.5 `document_validations`

Tabela append-only.

| Campo | Descrição |
|---|---|
| id | UUID |
| document_id | artefato validado |
| extraction_id | extração usada |
| policy_version_id | política aplicada |
| result | VALID, INVALID, EXPIRED, ILLEGIBLE ou QUARANTINED |
| validator_type | RULE, LLM ou HUMAN |
| validator_name | regra, modelo ou usuário |
| evidence | verificações e resultados |
| validated_at | data da decisão |

A validação corrente é a mais recente para a política vigente. Resultado de
LLM nunca substitui sozinho uma regra determinística disponível.

### 8.6 `document_policies`

Versões imutáveis da política documental.

| Campo | Descrição |
|---|---|
| id | UUID |
| version | código único legível |
| status | DRAFT, ACTIVE ou RETIRED |
| effective_from | início da vigência |
| effective_until | fim opcional |
| rules | JSONB com requisitos e parâmetros |
| created_by | usuário responsável |
| created_at | data de criação |

Uma versão `ACTIVE` não pode ser editada. Mudança cria nova versão. A ativação
enfileira recomputação dos requisitos das operações ainda dentro da janela de
decisão.

Configuração inicial obrigatória:

```text
balanco_required_from_month = 4
document_source_freshness_minutes = 720
upload_token_ttl_hours = 24
document_job_max_attempts = 5
document_job_lease_minutes = 15
societary_auto_release_enabled = false durante shadow mode
```

### 8.7 `operation_requirements`

| Campo | Descrição |
|---|---|
| id | UUID |
| operation_id | operação |
| policy_version_id | política que criou o requisito |
| code | código semântico do requisito |
| purpose | SCORE, PROPOSAL_GATE ou DISBURSEMENT |
| holder_type | LEGAL_ENTITY ou PERSON |
| holder_document | CNPJ ou CPF esperado |
| mandatory | se bloqueia seu propósito |
| status | PENDING, SATISFIED, NOT_APPLICABLE ou WAIVED |
| pending_reason | MISSING, INVALID, EXPIRED, ILLEGIBLE, SOURCE_UNKNOWN ou CONFLICT |
| revision | incremento monotônico por mudança efetiva |
| waived_by | diretor, quando aplicável |
| waiver_reason | justificativa |
| waiver_expires_at | validade do waiver |
| created_at | data de criação |
| updated_at | última recomputação |

`INVALID` e `EXPIRED` são resultados das evidências. O requisito continua
`PENDING`, com `pending_reason` correspondente.

O requisito é `SATISFIED` quando todos os seus itens obrigatórios estão
satisfeitos. Um requisito sem itens concluídos nunca é satisfeito por ausência
de evidência.

### 8.8 `requirement_items`

Define as peças esperadas dentro de um requisito composto.

| Campo | Descrição |
|---|---|
| id | UUID |
| requirement_id | requisito pai |
| code | código estável do item |
| evidence_role | consolidado, alteração, procuração, identidade etc. |
| holder_type | LEGAL_ENTITY ou PERSON |
| holder_document | CNPJ ou CPF esperado |
| accepted_type_codes | tipos documentais aceitos |
| satisfaction_rule | ALL, ANY ou MIN_COUNT |
| min_count | usado em MIN_COUNT |
| mandatory | se é necessário para concluir o requisito pai |
| status | PENDING ou SATISFIED |
| pending_reason | motivo estruturado |

O requisito societário cria um item para o instrumento consolidado e itens
adicionais para alterações, atas ou procurações identificadas no catálogo ou
referenciadas pelos documentos. A ausência de uma peça referenciada cria item
`PENDING`; não é tratada como se a peça não fosse necessária.

### 8.9 `requirement_evidences`

Relacionamento muitos-para-muitos entre itens de requisito e documentos.

| Campo | Descrição |
|---|---|
| requirement_item_id | item do requisito |
| document_id | artefato candidato |
| validation_id | validação usada |
| evidence_role | papel no conjunto documental |
| accepted | se conta para satisfação |
| rejection_reason | motivo da rejeição |

Um item `ALL` pode exigir todas as peças identificadas de uma classe. Um item
`ANY` pode aceitar CIN, RG ou CNH para a identidade. A regra do requisito pai
continua sendo a satisfação de todos os seus itens obrigatórios.

### 8.10 `signature_arrangements`

| Campo | Descrição |
|---|---|
| id | UUID |
| operation_id | operação |
| policy_version_id | política aplicada |
| corporate_requirement_id | conjunto societário usado |
| status | CANDIDATE, VALIDATED, REJECTED ou NEEDS_REVIEW |
| validation_mode | AUTOMATIC ou HUMAN |
| priority | ordem determinística de preferência |
| is_preferred | arranjo escolhido para coleta e formalização |
| maximum_authorized_amount | limite extraído, quando houver |
| powers_valid_until | validade de mandato ou procuração |
| evidence | cláusulas e páginas que sustentam o arranjo |
| created_at | data de criação |

### 8.11 `signature_arrangement_members`

| Campo | Descrição |
|---|---|
| arrangement_id | arranjo |
| person_cpf | CPF |
| person_name | nome |
| role | administrador, procurador ou representante |
| identity_requirement_id | requisito nominal de identidade |
| signature_order | ordem, quando relevante |

Um arranjo está `VALIDATED` somente quando todas as pessoas necessárias têm
poderes válidos para o valor da operação e requisitos de identidade
`SATISFIED`.

### 8.12 `document_jobs`

Fila durável para todos os trabalhos documentais.

| Campo | Descrição |
|---|---|
| id | UUID |
| job_type | REFRESH_SOURCE, INGEST_FILE, SCAN, EXTRACT, VALIDATE, RECOMPUTE_REQUIREMENTS, MATERIALIZE, REPROCESS_SCORE, PRE_SEND_CHECK ou RECONCILE_SEND |
| operation_id | operação, quando aplicável |
| document_id | documento, quando aplicável |
| idempotency_key | chave única do trabalho lógico |
| payload | JSONB mínimo |
| status | PENDING, RUNNING, RETRY, COMPLETED, FAILED ou CANCELLED |
| attempts | tentativas executadas |
| max_attempts | limite |
| next_attempt_at | próxima tentativa |
| locked_by | consumidor atual |
| locked_until | lease |
| result | JSONB mínimo do desfecho |
| last_error | erro sanitizado |
| started_at | início |
| completed_at | fim |
| created_at | criação |

O claim deve ocorrer em função SQL/RPC transacional, com comportamento
equivalente a `FOR UPDATE SKIP LOCKED`. Atualizações de conclusão exigem o
mesmo `locked_by`, evitando que um worker antigo conclua uma lease já retomada.
Jobs que podem exceder a duração da lease devem renová-la periodicamente. A
renovação também exige o mesmo `locked_by`.

### 8.13 `proposal_attempts`

| Campo | Descrição |
|---|---|
| id | UUID |
| operation_id | operação |
| attempt_number | contador |
| idempotency_key | chave enviada à origem, se suportada |
| request_hash | hash do payload canônico |
| gate_revision | revisão aprovada pelo gate |
| status | CREATED, SENDING, CONFIRMED, REJECTED, AMBIGUOUS ou RECONCILED |
| external_proposal_id | identificador Broadfactor |
| http_status | código, quando conhecido |
| response_summary | resposta sem dados sensíveis |
| error_message | erro sanitizado |
| created_at | criação |
| completed_at | conclusão |

### 8.14 Revisões, decisões e estado da proposta

Adicionar a `operations`:

| Campo | Descrição |
|---|---|
| document_revision | BIGINT, inicia em 0 e cresce quando qualquer estado documental efetivo muda |
| document_score_revision | BIGINT, inicia em 0 e cresce somente quando um insumo documental do score muda |
| current_score_revision_id | UUID da execução corrente do score |
| credit_decision_status | PENDING, APPROVED ou REJECTED |
| current_credit_decision_id | decisão vigente |
| proposal_status | máquina de estados da seção 5.2 |

Cada execução bem-sucedida de `score_engine` recebe `score_revision_id` novo e
registra `input_document_score_revision`. Esses campos também devem ser
preservados em `score_snapshot_versions`.

Invariantes obrigatórios:

```text
document_revision
  incrementa se, e somente se, mudar o estado documental efetivo usado pelo
  gate, pelos arranjos ou pelo score

document_score_revision
  incrementa se, e somente se, mudar a projeção documental consumida pelo score
```

Todo incremento de `document_score_revision` implica incremento simultâneo de
`document_revision`; o inverso não é verdadeiro. Mudanças apenas em identidade,
poderes ou arranjos incrementam `document_revision`, mas não
`document_score_revision`.

Os hashes canônicos das duas projeções devem ser comparados e as revisões devem
ser atualizadas na mesma função SQL/RPC transacional. Alteração de timestamp,
log ou metadado sem efeito decisório não incrementa nenhuma revisão. O score é
obsoleto somente quando seu `input_document_score_revision` difere de
`operations.document_score_revision`.

Evoluir `operation_approvals` como histórico append-only da decisão:

| Campo | Descrição |
|---|---|
| score_revision_id | score sobre o qual a decisão foi tomada |
| document_revision | revisão documental correspondente |
| document_score_revision | revisão dos insumos documentais do score |
| approved_amount | exposição máxima aprovada |
| approved_term_months | prazo máximo aprovado |
| minimum_rate | piso de taxa aprovado, quando aplicável |
| approved_rating | rating conhecido na decisão |
| approved_pd | PD conhecida na decisão |
| approved_lgd | LGD conhecida na decisão |
| decision_source | SYSTEM ou HUMAN |
| decision_reason_code | motivo estruturado da decisão |
| valid_until | validade da decisão |
| superseded_at | quando deixou de ser vigente |
| superseded_reason | motivo estruturado |

Valores iniciais de `decision_reason_code` usados pela coleta documental:

```text
SYSTEM_SCORE_BELOW_POLICY
SYSTEM_HARD_GATE
HUMAN_REJECTION
SCORE_OU_TERMOS_ALTERADOS
```

Somente `SYSTEM_SCORE_BELOW_POLICY` é elegível à coleta contrafactual após
rejeição. Novos códigos devem declarar explicitamente se permitem essa coleta;
o default é não permitir.

Criar `operation_proposals`, uma linha corrente por operação, com status,
lista de blockers, `gate_revision`, `score_revision_id`, `document_revision`,
IDs dos arranjos de assinatura autorizados, último attempt e timestamps.
`proposal_attempts` permanece como histórico imutável das tentativas externas.

### 8.15 `document_exceptions`

Fila explícita de intervenção humana. Não inferir a fila apenas consultando
várias tabelas com estados de erro.

| Campo | Descrição |
|---|---|
| id | UUID |
| operation_id | operação afetada |
| exception_type | tipo estruturado |
| source_type | DOCUMENT, REQUIREMENT, ARRANGEMENT, JOB ou PROPOSAL_ATTEMPT |
| source_id | registro que originou a exceção |
| status | OPEN, CLAIMED, RESOLVED ou DISMISSED |
| priority | prioridade calculada |
| assigned_to | usuário responsável |
| resolution | decisão e justificativa |
| created_at | criação |
| resolved_at | conclusão |

A criação é idempotente por fonte e motivo enquanto houver exceção aberta.
Falha transitória ainda dentro do limite de retry não cria exceção humana.

### 8.16 Evolução de `upload_tasks`

A tabela existente deve deixar de ser específica para uma certidão.

| Campo | Descrição |
|---|---|
| requirement_id | requisito solicitado |
| token_hash | hash do token, substitui token em texto puro |
| allowed_type_codes | tipos aceitos |
| max_files | quantidade permitida |
| status | PENDING, COMPLETED, EXPIRED ou REVOKED |
| attempts | tentativas de envio |
| expires_at | validade |
| revoked_at | revogação |
| completed_at | conclusão |

O token em texto puro existe somente na resposta de criação da tarefa. O
backfill revoga tokens legados; eles não devem ser copiados para `token_hash`.

## 9. Migração e compatibilidade

1. Adicionar `type_code` e os novos campos à tabela `documents` sem remover o
   enum legado inicialmente.
2. Fazer backfill dos documentos atuais, usando `operation_id` para obter o
   CNPJ titular.
3. Calcular `content_sha256` a partir do objeto armazenado. Registros cujo
   objeto não exista ficam em quarentena e não satisfazem requisitos.
4. Adaptar leitores para preferir `type_code`, com fallback temporário para
   `document_type`.
5. Ativar escrita somente no modelo novo.
6. Adicionar revisões a operações e snapshots de score, atribuindo revisão 0
   aos registros históricos.
7. Fazer backfill de `credit_decision_status`: operações `approved` viram
   `APPROVED`; operações `rejected` viram `REJECTED`; as demais viram
   `PENDING`. Nenhuma operação histórica é considerada proposta enviada.
8. Criar requisitos históricos por job apenas para operações ainda dentro da
   janela de decisão. Operações encerradas não recebem pendências órfãs.
9. Revogar tokens de upload legados armazenados em texto puro.
10. Remover o fallback legado apenas após verificar que não existem documentos
   sem migração.

Migrações estruturais não devem baixar arquivos nem chamar LLM. Backfills de
conteúdo são jobs observáveis e retomáveis.

## 10. Política documental inicial

### 10.1 Requisitos de gate

| Código | Regra |
|---|---|
| CORPORATE_AUTHORITY_SET | conjunto societário suficiente ou waiver válido |
| VALID_SIGNATURE_ARRANGEMENT | pelo menos um arranjo `VALIDATED` |
| SIGNER_IDENTITIES | identidade válida de todos os membros de ao menos um arranjo |

O gate não exige a escolha antecipada de uma única pessoa. Ele exige ao menos
um caminho completo de assinatura. A interface deve mostrar à Broadfactor e ao
time interno quais arranjos foram validados.

Quando o instrumento societário for dispensado, o waiver deve informar
explicitamente as pessoas autorizadas, forma de assinatura, limite de valor e
validade. Ele cria um arranjo `HUMAN` restrito àquela operação. As identidades
de todos os membros continuam obrigatórias. Assim, o waiver é operacionalmente
utilizável sem se transformar em liberação genérica de qualquer signatário.

### 10.2 Requisitos de score

| Código | Efeito inicial |
|---|---:|
| CND_FEDERAL_VALID | ausência: -6 pontos |
| CNDT_VALID | ausência: -6 pontos |
| CRF_FGTS_VALID | ausência: -6 pontos |
| BALANCE_LAST_FISCAL_YEAR_VALID | ausência: -10 pontos |
| BALANCE_PREVIOUS_FISCAL_YEAR_VALID | sem penalidade nesta versão |
| CURRENT_TRIAL_BALANCE_VALID | sem penalidade nesta versão |
| REVENUE_12M_VALID | somente flag |

As penalidades continuam parametrizadas no banco. Ausência, invalidez,
ilegibilidade e vencimento mantêm a penalidade, mas geram flags distintas.

### 10.3 Exercício contábil exigido

O último e o penúltimo exercício são derivados da mesma data de corte e se
deslocam juntos. Considerando `current_year`, `current_month` e o parâmetro
`balanco_required_from_month`:

```text
required_last_year =
  current_year - 2, se current_month < balanco_required_from_month
  current_year - 1, se current_month >= balanco_required_from_month

required_previous_year = required_last_year - 1
```

Com o corte inicial em abril, até março o último exercício exigível é o
encerrado dois anos antes do ano corrente. A partir de abril, passa a ser o
exercício encerrado no ano anterior. O requisito de penúltimo exercício muda na
mesma data.

Exemplo em 2026:

```text
01/01/2026 a 31/03/2026 -> último 2024, penúltimo 2023
01/04/2026 a 31/12/2026 -> último 2025, penúltimo 2024
```

Assim, a ausência do balanço de 2025 recebe a penalidade de 10 pontos de abril
a dezembro de 2026. O penúltimo exercício continua sem penalidade nesta versão,
mas é rastreado com o ano correto.

Documento de exercício futuro recebido antes da data de corte pode ser
armazenado e validado antecipadamente. Ele não substitui o requisito corrente;
passa a satisfazer automaticamente o novo requisito quando a política virar,
sem exigir novo upload.

O mês de corte é parâmetro da política. Empresas com exercício social diferente
do ano civil exigem regra específica e são encaminhadas para exceção enquanto
essa regra não estiver implementada.

### 10.4 Validade nominal por tipo

O reaproveitamento usa a abrangência extraída e validada:

- instrumento societário: raiz de CNPJ, salvo evidência em contrário;
- CND Federal, CNDT e CRF/FGTS: CNPJ ou grupo explicitamente coberto;
- balanço, balancete e faturamento: entidade indicada no documento;
- identidade e IR: CPF exato.

Nunca inferir abrangência de certidão apenas pela raiz do CNPJ.

## 11. Ingestão documental

### 11.1 Broadfactor

`documentos_da_cotacao()` deve retornar um resultado estruturado contendo:

```text
outcome: OK | EMPTY | FORBIDDEN | ERROR
documents: lista
error: mensagem sanitizada opcional
```

Não chamar a API durante importação de módulo ou startup. O refresh é feito
somente dentro de job.

O catálogo deve preservar documentos da empresa e das pessoas, incluindo tipo,
identificador, titular e payload original sem base64. O base64 é mantido apenas
em memória durante validação de tamanho, hash e upload para R2.

Para cada item:

1. normalizar metadados;
2. calcular tamanho estimado antes do decode;
3. rejeitar tamanho acima da política;
4. decodificar;
5. verificar magic bytes;
6. calcular SHA-256;
7. deduplicar;
8. armazenar em quarentena no R2;
9. enfileirar scan;
10. somente após scan aprovado, enfileirar extração.

### 11.2 Upload manual

O upload manual é uma via permanente de exceção. A tarefa de upload é criada
para um requisito específico e aceita um ou mais arquivos conforme sua regra de
satisfação.

O token externo:

- é aleatório e possui entropia mínima de 256 bits;
- é armazenado apenas como hash;
- é limitado à tarefa e aos tipos permitidos;
- expira em 24 horas por padrão;
- pode ser revogado;
- não é registrado em logs ou audit trail;
- deixa de aceitar arquivos após a tarefa ser concluída ou revogada.

O ator do envio é registrado como `external_token` e referencia a tarefa. Isso
não equivale a identidade civil do remetente.

### 11.3 Segurança do arquivo

- tamanho máximo configurável por tipo;
- MIME determinado por conteúdo;
- ZIP com limite de arquivos, profundidade e tamanho descompactado;
- proteção contra zip bomb e PDF malformado;
- antivírus ou serviço de scanning obrigatório;
- objeto em quarentena inacessível aos usuários;
- liberação para leitura somente após scan aprovado;
- falha de scan é fechada: não valida nem satisfaz requisito.

A escolha entre ClamAV e serviço externo é decisão técnica da fase de
infraestrutura. O contrato acima é obrigatório independentemente da solução.

## 12. Extração e validação

### 12.1 Princípio

LLM e OCR extraem fatos. Regras determinísticas decidem validade sempre que
existir regra objetiva.

### 12.2 Certidões

Extrair e conferir:

- tipo da certidão;
- CNPJ e abrangência;
- emissor;
- resultado;
- data de emissão;
- validade;
- código ou URL de autenticidade, quando houver.

São aceitáveis `NEGATIVA` e `POSITIVA_COM_EFEITOS_DE_NEGATIVA`. Outros
resultados não satisfazem o requisito. Quando existir consulta pública de
autenticidade tecnicamente utilizável, ela deve prevalecer sobre o texto do
PDF.

### 12.3 Documentos financeiros

Extrair e conferir:

- CNPJ e razão social;
- período inicial e final;
- exercício;
- tipo da demonstração;
- assinatura e identificação do contador, quando disponível;
- integridade mínima das páginas e demonstrações.

O balanço só satisfaz `BALANCE_LAST_FISCAL_YEAR_VALID` quando o exercício
calculado pela política coincide com o exercício validado.

### 12.4 Identidade

Tipos iniciais aceitos: CIN, RG e CNH. A política pode ativar outros tipos.

Extrair e conferir:

- nome;
- CPF, quando impresso;
- número do documento;
- emissor;
- validade quando aplicável;
- legibilidade mínima.

O nome deve corresponder ao membro do arranjo. Quando o documento não contiver
CPF, a associação nominal ambígua vai para revisão humana.

### 12.5 Estrutura societária

O conjunto relevante pode conter instrumento consolidado, alterações
posteriores, atas e procurações. A extração deve produzir:

- empresa e raiz de CNPJ;
- administradores e representantes;
- CPF quando presente;
- forma de representação individual ou conjunta;
- limite de valor;
- atos que exigem aprovação adicional;
- início e fim de mandato;
- poderes de outorga de procuração;
- poderes e validade de cada procuração;
- páginas e trechos que sustentam cada fato;
- conflitos e lacunas.

### 12.6 Liberação societária automática

Durante o rollout, o resultado opera em `shadow mode`. Depois da habilitação da
política, um arranjo pode ser liberado automaticamente somente quando todas as
condições forem verdadeiras:

- documentos passaram por scan;
- texto é legível e não depende de OCR marcado como baixa qualidade;
- conjunto contém instrumento declarado como consolidado;
- catálogo da origem foi consultado com sucesso;
- não há documento posterior não analisado;
- evidências são citadas e localizáveis;
- não há conflito entre cláusulas;
- forma de assinatura é inequívoca;
- mandatos e procurações estão vigentes;
- limite de valor cobre o valor da proposta;
- todos os membros têm identidade válida.

Casos fora dessas condições recebem `NEEDS_REVIEW`. Confiança autodeclarada
pelo modelo não é critério de liberação.

A ativação de `societary_auto_release_enabled` exige amostra mínima de 100 casos
revisados em shadow mode, zero erro crítico de poderes e precisão mínima de 99%
nos campos usados pelo gate.

## 13. Requisitos e arranjos de assinatura

Ao criar ou atualizar uma operação, o sistema:

1. seleciona a política vigente;
2. cria requisitos empresariais;
3. procura documentos reutilizáveis;
4. valida o conjunto societário;
5. gera todos os arranjos de assinatura suportados pelas cláusulas;
6. elimina arranjos incompatíveis com valor, prazo ou mandato;
7. ranqueia os restantes, preferindo assinatura individual, menos pessoas e
   maior cobertura documental já disponível;
8. marca um arranjo como preferencial;
9. cria requisitos nominais de identidade inicialmente apenas para os membros
   do arranjo preferencial;
10. marca como `VALIDATED` cada arranjo completamente comprovado;
11. envia para revisão somente arranjos ambíguos ou conflitantes.

Não é necessário escolher um signatário antes de enviar a proposta. O gate é
satisfeito quando há pelo menos um arranjo validado. A decisão e o payload de
envio registram os IDs dos arranjos permitidos. A formalização deve usar um
deles; qualquer pessoa diferente exige nova validação.

A integração com a Broadfactor deve transmitir ou disponibilizar, de forma
inequívoca, os nomes, CPFs e combinações autorizadas. A Broadfactor deve
confirmar qual arranjo foi usado ou devolver a nota assinada para conferência.
Enquanto não existir esse retorno, o Credit Engine consegue validar antes do
envio, mas não consegue provar automaticamente que a formalização respeitou o
arranjo. Essa limitação bloqueia automação do desembolso, não a construção do
módulo documental.

Se a Broadfactor precisar usar um arranjo alternativo, o Credit Engine cria os
requisitos de identidade correspondentes e só autoriza esse arranjo depois que
todos forem satisfeitos. Isso evita coletar documentos pessoais de todos os
representantes possíveis sem necessidade.

### 13.1 Solicitação automática de documentos

O sistema cria tarefas de upload sem depender de ação interna quando:

- a Broadfactor respondeu `OK` ou `EMPTY` e o requisito continua sem evidência;
- não existe job de ingestão pendente para o mesmo requisito;
- a operação ainda pode seguir para crédito ou proposta;
- o documento é obrigatório para o gate ou tem penalidade ativa no score.

Documentos pessoais só são solicitados para o arranjo preferencial. IR só é
solicitado quando `aval_requerido=true`.

A notificação usa contato previamente disponível e identificado na operação ou
na Broadfactor. Se não houver contato válido, cria exceção `CONTACT_MISSING`.
Reenvio respeita limite e intervalo configuráveis. O time interno pode revogar,
reenviar ou substituir o destinatário, mas essas ações não são necessárias no
caminho normal.

Uma operação rejeitada não gera coleta apenas porque o `rating_potencial` é
maior que o rating efetivo. A coleta automática após rejeição exige todas as
condições:

- a decisão foi produzida pelo sistema, com `decision_source=SYSTEM`;
- o motivo é score abaixo da política, e não gate determinístico de recusa;
- não existe rejeição humana vigente;
- o contrafactual considera somente penalizações de documentos que podem ser
  efetivamente solicitados e validados;
- o score e o rating contrafactuais cruzam a faixa mínima aprovável definida
  pela política de crédito.

Definições:

```text
score_potencial_documental =
  score efetivo
  + penalizações recuperáveis dos requisitos documentais pendentes

coleta_elegivel =
  score_potencial_documental >= score mínimo aprovável
  E rating_potencial_documental pertence às faixas aprováveis
```

Não basta haver melhora; o contrafactual precisa mudar a decisão. Quando mais
de um conjunto de documentos puder cruzar o limiar, solicitar primeiro o
conjunto mínimo de evidências e dados pessoais. Uma rejeição humana somente
pode voltar a gerar coleta depois de reabertura explícita por usuário com
alçada.

## 14. Materialização e score

### 14.1 Fonte de verdade

O score deve consumir uma projeção determinística dos
`operation_requirements` correntes, nunca a mera existência de uma linha em
`documents`.

### 14.2 Compatibilidade com certidões

Durante a transição, o materializador mantém os snapshots `cnd_federal`,
`cndt_tst` e `fgts` para compatibilidade. Cada snapshot materializado inclui:

```text
document_id
validation_id
requirement_id
policy_version
resultado
valid_until
materialized_revision
```

Quando o requisito deixa de estar satisfeito, o snapshot sai de `completed` e
recebe o motivo correspondente. A ausência permanece não bloqueante para o
pipeline.

### 14.3 Balanço

O score deixa de buscar tipos brutos na tabela `documents`. A penalização por
balanço usa exclusivamente o requisito `BALANCE_LAST_FISCAL_YEAR_VALID` na
projeção materializada. Documento inválido, vencido, de outro CNPJ ou de outro
exercício não remove a penalização.

### 14.4 Reprocessamento

Após recomputar o lote:

1. comparar o estado documental efetivo com o anterior;
2. se qualquer requisito ou arranjo mudou, incrementar `document_revision`;
3. comparar separadamente a projeção consumida pelo score;
4. se o subconjunto de score não mudou, não reprocessar;
5. se mudou, incrementar `document_score_revision` e marcar o score obsoleto;
6. enfileirar um único `REPROCESS_SCORE` por operação e revisão de score;
7. executar somente `score_engine` e pricing;
8. não refazer web research, Serasa ou consultas externas pagas;
9. preservar a versão anterior do snapshot de score;
10. avaliar a validade da decisão de crédito existente.

A chave idempotente é
`reprocess-score:{operation_id}:{document_score_revision}`. Mudança apenas de
identidade ou poderes atualiza o gate, mas não recalcula o score.

Documento novo, nova validação, supersessão ou vencimento deve localizar todos
os requisitos abertos compatíveis com o titular e enfileirar recomputação para
cada operação ainda não enviada nem expirada. A propagação é idempotente e não
se limita à operação em que o arquivo foi originalmente recebido.

## 15. Orquestração, retry e recuperação

### 15.1 Descoberta e execução

O job já acionado duas vezes ao dia deve:

1. rodar o watchdog de operações;
2. enfileirar refresh documental elegível;
3. executar a ingestão de novas cotações.

Um dispatcher durável drena `document_jobs` prontos em intervalo máximo de 60
segundos. A cadência de descoberta da Broadfactor continua duas vezes ao dia;
retries de processamento não esperam a próxima rodada.

### 15.2 Retry

- falhas de rede, 429 e 5xx: retry exponencial com jitter;
- arquivo inválido, tipo não suportado e conflito determinístico: falha
  permanente, sem retry automático;
- lease vencida: novo worker pode reivindicar;
- conclusão por worker antigo: recusada pelo token da lease;
- após `max_attempts`: job `FAILED` e item visível na fila de exceções.

O primeiro retry ocorre em aproximadamente 1 minuto e o intervalo máximo é 30
minutos.

### 15.3 Refresh

Gatilhos:

- rotina Broadfactor duas vezes ao dia;
- documento próximo do vencimento;
- virada do exercício exigível;
- ativação de nova política;
- upload concluído;
- solicitação manual;
- pre-check de envio.

O refresh periódico de catálogo prioriza operações com pendência e dentro da
janela de decisão. Operações documentalmente completas recebem refresh forçado
no pre-check de envio, evitando confiar indefinidamente em um catálogo antigo.

Um `ERROR` ou `FORBIDDEN`:

- registra o source snapshot;
- preserva evidências válidas existentes;
- gera flag de fonte indisponível;
- não transforma automaticamente requisito satisfeito em ausente;
- pode bloquear o envio apenas quando a política exigir frescor da fonte e não
  houver evidência manual validada suficiente.

Expiração baseada em `valid_until` é processada localmente e não depende de
nova consulta à Broadfactor.

Quando um catálogo `OK` deixa de listar um `external_id` visto anteriormente,
o artefato local não é apagado. O sistema registra `SOURCE_MISMATCH`. Para
documento societário usado no gate, a divergência abre exceção e impede nova
formalização até reconciliação; para documento com validade própria, a política
decide se a validade local continua suficiente.

## 16. Gate e envio de proposta

### 16.1 Precondições

O gate server-side exige, na mesma revisão:

- decisão de crédito `APROVADA` e ainda válida;
- score não obsoleto;
- `score.input_document_score_revision = operations.document_score_revision`;
- requisitos obrigatórios satisfeitos ou waiver permitido e vigente;
- pelo menos um arranjo de assinatura `VALIDATED`;
- ausência de job documental ou reprocessamento que possa alterar o gate;
- margem, prazo, valor e vigência revalidados pela W6;
- operação e cotação não expiradas;
- inexistência de tentativa de envio ativa ou ambígua.

### 16.2 Claim transacional

Uma função SQL/RPC deve:

1. bloquear logicamente a proposta;
2. conferir todas as precondições;
3. criar `proposal_attempts` com request hash e gate revision;
4. mudar a proposta de `PRONTA` para `ENVIANDO` condicionalmente;
5. retornar o payload canônico a enviar.

Falha em qualquer precondição não cria tentativa e retorna código estruturado.
Não realizar uma sequência de leituras e updates independentes via REST.

### 16.3 Resultado HTTP

- sucesso confirmado: `ENVIADA` e attempt `CONFIRMED`;
- 4xx definitivo com resposta válida: `FALHA_ENVIO` e attempt `REJECTED`;
- falha antes de transmitir o corpo: pode retornar a `PRONTA` conforme retry;
- timeout, desconexão ou 5xx após possível transmissão: `ENVIO_INDETERMINADO` e
  attempt `AMBIGUOUS`.

Nunca reenviar automaticamente uma tentativa ambígua.

### 16.4 Reconciliação

Se a Broadfactor suportar idempotency key, usar a chave da tentativa em todos
os retries. Se houver endpoint de consulta, um job `RECONCILE_SEND` verifica a
existência da proposta pelo identificador ou request hash.

Sem idempotência e sem consulta confiável, `ENVIO_INDETERMINADO` entra na fila
de exceções para conferência com a Broadfactor. Esta é uma exceção legítima ao
objetivo de zero intervenção humana.

## 17. APIs

Todos os endpoints abaixo usam JWT e a guarda global, exceto uploads externos
por token e jobs internos por `X-Internal-Token`.

### 17.1 Operação e status documental

```text
GET  /api/v1/operations/{id}/document-status
GET  /api/v1/operations/{id}/documents
POST /api/v1/operations/{id}/documents/refresh
POST /api/v1/operations/{id}/requirements/{requirement_id}/waive
POST /api/v1/operations/{id}/requirements/{requirement_id}/revoke-waiver
GET  /api/v1/operations/{id}/signature-arrangements
POST /api/v1/operations/{id}/signature-arrangements/{arrangement_id}/review
POST /api/v1/operations/{id}/proposal/send
GET  /api/v1/operations/{id}/proposal-attempts
```

Refresh manual é assíncrono e retorna 202. Envio exige role e alçada já
definidas pela política de operações. Revisão de poderes exige gerente ou
diretor. Waiver exige diretor.

### 17.2 Arquivos

```text
POST /api/v1/operations/{id}/upload-tasks
POST /api/v1/uploads/{token}
GET  /api/v1/documents/{document_id}/download
GET  /api/v1/documents/{document_id}/validations
```

O endpoint de download valida acesso e retorna URL assinada curta. Nunca expõe
`storage_key` como URL pública.

### 17.3 Exceções

```text
GET  /api/v1/document-exceptions
GET  /api/v1/document-exceptions/{id}
POST /api/v1/document-exceptions/{id}/resolve
```

### 17.4 Jobs internos

```text
POST /api/v1/internal/documents/discover
POST /api/v1/internal/document-jobs/drain
POST /api/v1/internal/document-jobs/recover-leases
```

Os endpoints internos retornam resumo estruturado e nunca expõem dados
pessoais em logs ou resposta de scheduler.

## 18. Interface

### 18.1 Detalhe da operação

Exibir separadamente:

- decisão de crédito;
- estado da proposta;
- revisão documental e revisão do score;
- requisitos de gate;
- documentos opcionais e efeitos no score;
- arranjos de assinatura validados;
- pendências e motivo;
- último refresh e outcome da fonte;
- jobs em andamento ou falhos.

O botão de envio permanece desabilitado na interface quando o gate não passa,
mas a proteção efetiva está no backend.

### 18.2 Upload

O sistema cria automaticamente solicitações para requisitos elegíveis. O
usuário interno pode criar, reenviar ou revogar uma solicitação em casos de
exceção. A página por token mostra somente empresa, requisito, tipos aceitos,
prazo e estado do upload. Não mostra score, taxa, documentos anteriores ou
dados de outros titulares.

### 18.3 Fila de exceções

Ordenação por impacto e idade. Deve distinguir:

```text
DOCUMENTO_ILEGIVEL
DOCUMENTO_INVALIDO
CONFLITO_SOCIETARIO
PODERES_INSUFICIENTES
FONTE_INDISPONIVEL
CONTACT_MISSING
JOB_ESGOTADO
ENVIO_INDETERMINADO
```

Cada item mostra evidência, ações permitidas e consequência da decisão. Ações
humanas exigem justificativa e geram audit trail.

## 19. Segurança, privacidade e retenção

1. Bucket R2 privado, sem URL permanente.
2. Criptografia em trânsito e em repouso.
3. Download autenticado, autorizado por operação e registrado.
4. Tokens externos armazenados como hash e nunca logados.
5. Base64, texto integral e dados pessoais não aparecem em logs.
6. RLS habilitado em todas as tabelas novas.
7. Acesso de usuários segue menor privilégio; service role somente no backend.
8. Exclusão remove objeto e metadados conforme política, salvo legal hold.
9. IR só é solicitado quando `aval_requerido=true`.
10. Ambientes não produtivos não recebem documentos reais, salvo processo
    aprovado de anonimização.

O prazo de retenção e as hipóteses de legal hold devem ser configurados antes
da ativação em produção, com validação do responsável por privacidade. O código
deve suportar `retention_until`, exclusão agendada e bloqueio por legal hold sem
embutir prazo fixo.

## 20. Auditoria

Eventos mínimos:

```text
document_received
document_deduplicated
document_scanned
document_extracted
document_validated
document_superseded
requirement_created
requirement_status_changed
upload_task_created
upload_task_notified
upload_task_revoked
requirement_waived
requirement_waiver_revoked
signature_arrangement_created
signature_arrangement_reviewed
document_refresh_completed
score_reprocessing_requested
credit_decision_invalidated
proposal_gate_passed
proposal_gate_rejected
proposal_send_started
proposal_send_confirmed
proposal_send_ambiguous
proposal_send_reconciled
document_downloaded
```

Cada evento registra ator, tipo de ator, operação, documento ou requisito,
valor anterior e novo, policy version, correlation ID e data. Payload não inclui
arquivo, token, base64 ou texto integral.

## 21. Observabilidade

Métricas:

- documentos descobertos por fonte e tipo;
- outcomes OK, EMPTY, FORBIDDEN e ERROR;
- taxa de deduplicação e reaproveitamento;
- tempo por etapa;
- taxa de scan, extração e validação;
- divergência entre validação automática e humana;
- jobs por status, retry e lease recuperada;
- requisitos pendentes por motivo;
- tarefas de upload criadas, entregues e sem contato;
- operações com score obsoleto;
- operações por estado de proposta;
- envios confirmados, rejeitados e ambíguos;
- percentual straight-through e percentual de exceção humana.

Alertas:

- lease vencida acima do limite;
- job `FAILED`;
- aumento de `FORBIDDEN` ou `ERROR` da Broadfactor;
- score obsoleto por mais de 15 minutos;
- proposta em `ENVIANDO` além do timeout;
- qualquer `ENVIO_INDETERMINADO`;
- taxa de erro crítico societário acima de zero no shadow mode.

## 22. Critérios de aceite

### 22.1 Reaproveitamento e idempotência

- Dado um CNPJ com certidão válida já armazenada, nova operação compatível
  deve satisfazer o requisito sem novo upload.
- Dois refreshes iguais não devem criar novo documento, validação, job ou
  reprocessamento.
- Upload repetido do mesmo arquivo deve retornar o artefato existente.
- Mesmo `external_id` com conteúdo diferente deve criar alerta de conflito e
  não sobrescrever o artefato anterior.

### 22.2 Validação

- Documento apenas armazenado não satisfaz requisito.
- Balanço de exercício errado não remove penalização.
- Certidão vencida deixa de satisfazer sem depender de nova chamada externa.
- Positiva com efeitos de negativa válida é aceita.
- Documento em quarentena ou ilegível nunca é materializado.
- Antes do mês de corte, último e penúltimo exercício correspondem a
  `current_year - 2` e `current_year - 3`.
- A partir do mês de corte, os dois mudam juntos para `current_year - 1` e
  `current_year - 2`.
- Balanço futuro validado antecipadamente não substitui o requisito vigente
  antes da data de corte.

### 22.3 Requisitos compostos

- Instrumento consolidado e alteração posterior satisfazem o requisito pai
  somente quando os dois itens obrigatórios estão satisfeitos.
- Um dos documentos de identidade permitidos satisfaz o item `ANY` da
  identidade.
- Arranjo de assinatura conjunta exige identidade válida de todos os membros.
- Procurador sem procuração vigente não forma arranjo válido.
- O sistema solicita primeiro somente as identidades do arranjo preferencial.
- Ausência de contato cria exceção sem perder nem concluir a tarefa de upload.

### 22.4 Score e decisão

- Mudança documental sem efeito no score não dispara reprocessamento.
- Mudança com efeito dispara exatamente um reprocessamento por revisão.
- Reprocessamento documental não executa componente pago já concluído.
- Score antigo permanece versionado.
- Piora de risco invalida decisão aprovada.
- Melhora que permanece no envelope preserva a aprovação.
- Mudança apenas em identidade ou poderes incrementa `document_revision`, não
  incrementa `document_score_revision` e não torna o score obsoleto.
- Mudança em certidão ou balanço que altera a projeção incrementa as duas
  revisões na mesma transação e torna o score obsoleto.
- Mudança de metadado sem efeito decisório não incrementa nenhuma revisão.
- Rejeição humana não gera solicitação automática de novos documentos.
- Rejeição sistêmica por score só gera coleta quando o contrafactual documental
  cruza a faixa mínima aprovável e não existe gate de recusa.
- Quando vários documentos puderem recuperar a operação, é solicitado primeiro
  o conjunto mínimo capaz de cruzar o limiar.

### 22.5 Concorrência e recuperação

- Dois workers não executam o mesmo job simultaneamente.
- Worker morto tem lease recuperada após o timeout.
- Worker antigo não conclui job retomado por outro.
- Reinício entre armazenamento e validação retoma da etapa correta.
- Refresh manual e agendado concorrentes convergem para o mesmo estado.

### 22.6 Gate e envio

- Chamada direta ao endpoint não contorna o gate.
- Gate rejeita score com revisão documental anterior.
- Gate rejeita waiver vencido.
- Gate rejeita operação sem arranjo de assinatura validado.
- Waiver societário sem pessoas e forma de assinatura não cria arranjo válido.
- Duplo clique ou requests concorrentes criam uma tentativa.
- Timeout ambíguo não gera retry automático.
- Reconciliação confirmada muda a proposta para `ENVIADA` sem novo POST.

### 22.7 Segurança

- Token expirado, revogado ou de outra tarefa é recusado.
- Token não aparece em logs nem audit trail.
- Arquivo reprovado no scan não pode ser baixado por usuário.
- URL assinada expira e não permite acesso a outro objeto.
- Acesso a documento gera auditoria.

## 23. Testes obrigatórios

1. Unitários para política, exercício, abrangência, requisitos `ALL` e `ANY`.
2. Unitários para geração e validação de arranjos individuais e conjuntos.
3. Unitários para canonicalização e hash do catálogo Broadfactor.
4. Contrato do cliente cobrindo OK, EMPTY, FORBIDDEN, ERROR e 200 vazio.
5. Integração de upload, R2, scan, extração, validação e materialização.
6. Integração de expiração e desmaterialização.
7. Integração de reprocessamento e preservação de score anterior.
8. Corridas de claim, lease, score e envio.
9. Falha injetada após POST externo e antes da persistência local.
10. E2E do caminho automático e de cada tipo de exceção humana.
11. Testes de autorização e RLS.
12. Testes de migração e backfill com documentos legados.
13. Testes de seleção do arranjo preferencial e coleta mínima de identidades.
14. Testes do corte contábil antes, no mês e depois da virada.
15. Testes independentes de `document_revision` e `document_score_revision`.
16. Testes do contrafactual documental para rejeição sistêmica, gate de recusa
    e rejeição humana.
14. Testes de criação, deduplicação, expiração e entrega de upload automático.

## 24. Rollout

### Fase 0 - Contrato com a Broadfactor

- mapear tipos atuais e desejados;
- confirmar abrangência e atualidade do catálogo;
- verificar identificadores externos;
- confirmar idempotência e reconciliação de proposta;
- definir como os arranjos autorizados são entregues à Broadfactor e como o
  arranjo efetivamente usado é devolvido;
- medir tamanho, custo e latência;
- resolver acesso ao download de contratos e documentos.

O modelo pode começar em paralelo, mas nenhuma automação de gate depende de
premissa não confirmada da origem.

### Fase 1 - Fundação de dados

- migrations das entidades novas;
- evolução de `documents`;
- policy versionada;
- audit trail e RLS;
- backfill assíncrono.

Sem alterar score nem gate.

### Fase 2 - Orquestração e armazenamento

- fila durável;
- leases e dispatcher;
- cliente com outcomes;
- R2, quarentena e scan;
- upload genérico.

### Fase 3 - Extração, validação e requisitos

- validadores de certidão e balanço;
- materialização reversível;
- reaproveitamento por titular;
- requisitos compostos;
- reprocessamento coalescido.

Executar em shadow mode e comparar com o comportamento atual.

### Fase 4 - Estrutura societária

- extração versionada;
- arranjos de assinatura;
- identidades nominais;
- seleção do arranjo preferencial e solicitação automática;
- revisão humana de exceções;
- interface de upload e exceções.

Auto release permanece desligado até cumprir os critérios da seção 12.6.

### Fase 5 - Revisões e consistência da decisão

- document revision;
- vínculo score-decisão;
- preservação automática de aprovação favorável;
- invalidação de decisão em piora;
- indicadores de score obsoleto.

### Fase 6 - Gate e proposta

- máquina de estados;
- pre-check transacional;
- proposal attempts;
- envio e reconciliação;
- ativação gradual do gate.

O gate só pode ser ativado depois que upload, materialização, fila de exceções
e arranjos de assinatura estiverem disponíveis.

### Fase 7 - Faturamento e aval

- faturamento como flag;
- coleta condicional de IR;
- `aval_requerido` na decisão e pricing;
- `aval_constituido` como condição de desembolso.

## 25. Dependências externas e decisões técnicas pendentes

Estas pendências não alteram o modelo, mas bloqueiam partes específicas do
rollout:

| Item | Responsável | Bloqueia |
|---|---|---|
| Tipos e garantia de completude do catálogo | Broadfactor/produto | auto release societário |
| Idempotency key ou consulta de proposta | Broadfactor | retry automático de envio ambíguo |
| Envio e confirmação do arranjo de assinatura usado | Broadfactor/produto | automação do desembolso |
| Disponibilidade do download | Broadfactor | ingestão automática |
| Solução de antivírus | Engenharia/infra | liberação de arquivos |
| Prazo de retenção e legal hold | Privacidade/jurídico | produção com documentos pessoais |
| Cobertura da autorização SCR | Jurídico/Broadfactor | futura integração de bureau |

Ausência de resposta sobre idempotência não autoriza retry cego. Ausência de
garantia de completude societária mantém o auto release desligado e direciona
esses casos para revisão.

## 26. Definição de pronto

O módulo só está pronto para produção quando:

1. migrations e backfill foram executados e reconciliados;
2. RLS, download autenticado e retenção foram validados;
3. fila durável recupera falhas e reinícios;
4. materialização e desmaterialização foram testadas;
5. score usa requisitos válidos, inclusive para balanço;
6. revisão documental está vinculada ao score e à decisão;
7. interface de upload e exceções está disponível;
8. gate server-side passa nos testes de corrida;
9. envio ambíguo não é repetido automaticamente;
10. dashboards e alertas operacionais estão ativos;
11. auto release societário cumpriu o limiar de shadow mode ou permanece
    explicitamente desabilitado;
12. o runbook de `ENVIO_INDETERMINADO` foi treinado com o time responsável.
