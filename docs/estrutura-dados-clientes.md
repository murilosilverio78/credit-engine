# W8 — Estrutura de dados de clientes

AntecipaGov Credit Engine · versão 2 · 09/09/2026

Especificação para separar a empresa tomadora do crédito, denominada **cliente**
neste documento, da operação de crédito. A implantação deve seguir o padrão
expand/contract: criar e popular sem consumir, validar em shadow mode, passar a
ler e somente então desativar estruturas substituídas.

Este documento descreve o modelo alvo e as regras de transição. Os blocos SQL
são referências de desenho; as migrations executáveis devem ser geradas em
arquivos próprios, idempotentes e nunca executadas automaticamente pelo deploy.

---

## 1. Definições

### 1.1 Cliente

Cliente é a pessoa jurídica tomadora do crédito, também chamada de cedente no
fluxo da AntecipaGov. Cada estabelecimento identificado por CNPJ é um cliente.

- matriz e filial são clientes distintos;
- `cnpj_raiz` permite consolidar estabelecimentos da mesma raiz;
- `cnpj_raiz` não equivale a grupo econômico;
- cedente pessoa física permanece fora do escopo desta versão.

### 1.2 Operação

Operação é uma solicitação concreta de crédito, vinculada a uma cotação e,
quando disponível, a um contrato administrativo. Valores solicitados,
enquadramento, margem, prazo, score, rating e taxa são dados da operação.

### 1.3 Escopo semântico e chave de consulta

O escopo de um dado é definido pela entidade sobre a qual ele fala, não pela
chave técnica exigida para consultá-lo.

Exemplo: a Broadfactor exige `cotacao_id` para consultar recebimentos, mas o
resultado representa o histórico financeiro da empresa. Esse resultado pode ser
armazenado como sinal do cliente, mantendo a cotação e a operação como
proveniência.

Cada componente deve declarar um dos seguintes escopos:

| Escopo | Significado |
|---|---|
| `CLIENTE` | Reutilizável para o mesmo CNPJ, sujeito a validade |
| `CNPJ_RAIZ` | Reutilizável para a raiz somente quando a regra de negócio permitir |
| `COTACAO` | Válido exclusivamente para uma cotação Broadfactor |
| `CONTRATO` | Válido para um contrato administrativo específico |
| `DOCUMENTO` | Válido conforme titular, abrangência e validade do documento |
| `OPERACAO` | Produzido especificamente para uma operação de crédito |

Classificação inicial:

| Dado ou componente | Escopo |
|---|---|
| `brasil_api` | `CLIENTE` |
| `pessoa_juridica` | `CLIENTE` |
| CEIS, CNEP, CEPIM e acordos de leniência | `CLIENTE` |
| quadro societário | `CLIENTE` |
| `recursos_recebidos` | `CLIENTE`, com cotação como proveniência |
| pesquisa reputacional | `CLIENTE`, com data de corte e TTL |
| catálogo de contratos | `CLIENTE` |
| extração de contrato específico | `CONTRATO` |
| match no Comprasnet | `CONTRATO` |
| certidões e demonstrações financeiras | `DOCUMENTO` |
| `margem_disponivel` | `COTACAO` |
| `valor_enquadrado`, prazo final e pricing | `OPERACAO` |
| `score_engine` | `OPERACAO` |

Somente resultados classificados como `CLIENTE` podem ser materializados em
`cliente_snapshots`. Um componente operacional nunca deve ser reutilizado apenas
porque o CNPJ coincide.

---

## 2. Objetivos

1. Manter uma fonte canônica do cadastro atual de cada empresa.
2. Reutilizar automaticamente dados válidos sem repetir consultas externas.
3. Preservar a versão exata dos dados usados em cada decisão de crédito.
4. Manter séries históricas de sinais financeiros, sanções e vínculos
   societários.
5. Consolidar limites aprovados por CNPJ e por raiz de CNPJ.
6. Diferenciar consulta vazia, consulta com dados e falha de consulta.
7. Permitir migração gradual sem interromper o pipeline atual.

## 3. Não objetivos desta versão

1. Modelar grupos econômicos entre CNPJs de raízes diferentes.
2. Substituir o módulo documental definido na W4.
3. Criar controle de saldo devedor, desembolso ou liquidação financeira.
4. Aplicar limite de exposição como regra de elegibilidade antes da decisão de
   política de crédito.
5. Incluir tomadores pessoa física.

---

## 4. Princípios e invariantes

### 4.1 Coluna para consulta, JSON para evidência

Campos usados em filtro, ordenação, relacionamento ou regra de negócio devem ser
colunas tipadas. Respostas brutas, detalhes extensos e metadados de auditoria
permanecem em JSONB.

### 4.2 A operação é uma fotografia histórica

`operations.cnpj` e `operations.razao_social` devem permanecer mesmo depois da
migração. Eles representam os dados observados na originação e não devem mudar
retroativamente quando o cadastro atual do cliente mudar.

### 4.3 Reprodutibilidade

Todo snapshot operacional reutilizado do cliente deve registrar o
`cliente_snapshot_id` exato consumido. Consultar apenas o cadastro atual não é
suficiente para reproduzir uma decisão antiga.

### 4.4 Três estados de resultado

Toda integração deve distinguir:

| Estado | Significado |
|---|---|
| `OK` | Consulta concluída com dados válidos |
| `EMPTY` | Consulta concluída e fonte declarou ausência de dados |
| `ERROR` | Não foi possível concluir ou interpretar a consulta |

`ERROR` nunca pode ser convertido em `EMPTY`. Um resultado com erro nunca
substitui automaticamente o último resultado válido.

### 4.5 Idempotência

Timestamp não é chave de idempotência. Toda coleta deve possuir
`collection_key` estável para a mesma execução. Retry com a mesma chave não cria
uma nova observação.

### 4.6 Concorrência

Troca de snapshot vigente, criação do cliente e vínculo com a operação devem ser
atômicos. Escritas REST sequenciais não são consideradas transação. Quando a
atomicidade for necessária, usar função PostgreSQL chamada por RPC.

### 4.7 Precedência de fontes

Uma coleta não pode sobrescrever cegamente o cadastro atual. Cada campo deve ser
materializado segundo política determinística de precedência e atualidade.

Regras mínimas:

- valor `NULL` não sobrescreve valor não nulo, salvo regra explícita;
- correção manual validada tem precedência até ser liberada;
- fonte oficial prevalece sobre fonte agregadora para o mesmo campo;
- conflito não resolvido mantém o valor vigente e registra a divergência;
- atualização sem mudança material não cria nova revisão cadastral.

### 4.8 Competências mensais

Competências são armazenadas como `DATE`, usando o primeiro dia do mês. O
formato `MM/AAAA` pertence somente à apresentação. Isso evita ordenação por mês
em vez de ano.

---

## 5. Catálogo de escopo dos componentes

A etapa 1 deve acrescentar o escopo semântico ao catálogo já existente:

```sql
ALTER TABLE component_config
  ADD COLUMN data_scope VARCHAR(20) NOT NULL DEFAULT 'OPERACAO',
  ADD COLUMN empty_result_authoritative BOOLEAN NOT NULL DEFAULT FALSE,
  ADD CONSTRAINT component_config_data_scope_check
  CHECK (data_scope IN (
    'CLIENTE', 'CNPJ_RAIZ', 'COTACAO',
    'CONTRATO', 'DOCUMENTO', 'OPERACAO'
  ));
```

O default conservador é `OPERACAO`. Cada componente só muda para `CLIENTE`
depois de a equipe confirmar que seu conteúdo é reutilizável para o mesmo CNPJ.
O default de `empty_result_authoritative` também é conservador: resposta vazia
não apaga o último dado válido até que o comportamento da fonte esteja validado.

O uso de `component_type` em `cliente_snapshots` é permitido, mas a aplicação e
uma validação no serviço devem rejeitar componentes cujo `data_scope` não seja
`CLIENTE`.

---

## 6. Modelo de dados

### 6.1 `clientes`

Registro corrente de cada empresa.

```sql
CREATE TABLE clientes (
  id                        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cnpj                      VARCHAR(14) UNIQUE NOT NULL,
  cnpj_raiz                 VARCHAR(8)
                            GENERATED ALWAYS AS (LEFT(cnpj, 8)) STORED,

  razao_social              TEXT,
  nome_fantasia             TEXT,
  situacao_cadastral        VARCHAR(30),
  data_situacao_cadastral   DATE,
  data_abertura             DATE,
  natureza_juridica         TEXT,
  regime_tributario         VARCHAR(30),
  porte                     VARCHAR(30),
  unidade                   VARCHAR(20),
  cnae_principal_codigo     VARCHAR(10),
  cnae_principal_descricao  TEXT,
  municipio                 TEXT,
  uf                        CHAR(2),
  capital_social            NUMERIC(15,2),

  cadastro_revision         BIGINT NOT NULL DEFAULT 0,
  field_provenance          JSONB NOT NULL DEFAULT '{}'::jsonb,
  primeiro_contato_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ultima_coleta_cadastral   TIMESTAMPTZ,

  created_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at                TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT clientes_cnpj_check CHECK (cnpj ~ '^[0-9]{14}$'),
  CONSTRAINT clientes_unidade_check
    CHECK (unidade IS NULL OR unidade IN ('HEAD_OFFICE', 'BRANCH')),
  CONSTRAINT clientes_uf_check CHECK (uf IS NULL OR uf ~ '^[A-Z]{2}$'),
  CONSTRAINT clientes_capital_check
    CHECK (capital_social IS NULL OR capital_social >= 0)
);

CREATE INDEX idx_clientes_cnpj_raiz ON clientes (cnpj_raiz);
CREATE INDEX idx_clientes_razao_social
  ON clientes USING gin (razao_social gin_trgm_ops);
```

`field_provenance` registra, por campo materializado, fonte, instante observado
e eventual override. Ele não é usado para busca; por isso pode permanecer em
JSONB.

Na primeira versão, a materialização cadastral aplica somente as seguintes
regras mínimas:

1. valor nulo não sobrescreve valor não nulo;
2. observação cujo `observed_at` seja anterior não sobrescreve observação mais
   recente;
3. correção manual validada, marcada com `locked = true`, não é sobrescrita por
   coleta automática;
4. conflito entre valores não nulos de fontes diferentes mantém o valor vigente
   e registra a divergência para acompanhamento;
5. remoção exige ausência autoritativa explícita; um simples `NULL` não
   representa exclusão.

Não haverá matriz completa de precedência por campo e fonte nesta versão. As
regras mais amplas de precedência descritas como princípio representam o modelo
alvo e só entram em vigor quando a política futura for especificada.

A atualização de `field_provenance` deve ser atômica, por função SQL ou
`jsonb_set`. Workers não podem ler e regravar o objeto inteiro, pois duas
materializações concorrentes poderiam perder atualizações de campos distintos.

Exemplo:

```json
{
  "razao_social": {
    "source": "BRASIL_API",
    "observed_at": "2026-09-09T12:00:00Z"
  },
  "porte": {
    "source": "MANUAL_VALIDADO",
    "observed_at": "2026-09-09T13:00:00Z",
    "locked": true
  }
}
```

### 6.2 `clientes_historico`

Preserva estados cadastrais anteriores somente quando campos materiais mudam.

```sql
CREATE TABLE clientes_historico (
  id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id         UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  cadastro_revision  BIGINT NOT NULL,
  registro           JSONB NOT NULL,
  campos_alterados   TEXT[] NOT NULL,
  fonte              VARCHAR(30) NOT NULL,
  alterado_por       UUID,
  alterado_em        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (cliente_id, cadastro_revision)
);

CREATE INDEX idx_clientes_historico_cliente
  ON clientes_historico (cliente_id, cadastro_revision DESC);
```

O trigger não deve reagir a mudanças apenas em `updated_at`,
`ultima_coleta_cadastral` ou `field_provenance` sem alteração do valor de
negócio. `fonte` e `alterado_por` devem ser fornecidos por função de atualização
controlada; um trigger genérico não consegue inferir esse contexto.

### 6.3 `cliente_snapshots`

Histórico de tentativas e resultados de componentes com escopo `CLIENTE`.

```sql
CREATE TABLE cliente_snapshots (
  id                     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id             UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  component              component_type NOT NULL,
  collection_key         UUID NOT NULL,

  status                 component_status NOT NULL,
  result_state           VARCHAR(10) NOT NULL,
  raw_result             JSONB,
  parsed_result          JSONB,

  collected_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  valid_until            TIMESTAMPTZ,
  is_current_usable      BOOLEAN NOT NULL DEFAULT FALSE,

  source_operation_id    UUID REFERENCES operations(id) ON DELETE SET NULL,
  source_cotacao_id      VARCHAR(100)
                         REFERENCES cotacoes_broadfactor(cotacao_id)
                         ON DELETE SET NULL,
  error_message          TEXT,
  duration_ms            INTEGER,
  created_at             TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cliente_snapshots_result_state_check
    CHECK (result_state IN ('OK', 'EMPTY', 'ERROR')),
  CONSTRAINT cliente_snapshots_error_check
    CHECK (
      result_state <> 'ERROR'
      OR error_message IS NOT NULL
    ),
  CONSTRAINT cliente_snapshots_current_check
    CHECK (
      NOT is_current_usable
      OR (status = 'completed' AND result_state IN ('OK', 'EMPTY'))
    ),
  CONSTRAINT cliente_snapshots_validity_check
    CHECK (valid_until IS NULL OR valid_until >= collected_at),
  CONSTRAINT cliente_snapshots_duration_check
    CHECK (duration_ms IS NULL OR duration_ms >= 0),
  UNIQUE (cliente_id, component, collection_key)
);

CREATE UNIQUE INDEX idx_cliente_snapshots_current_usable
  ON cliente_snapshots (cliente_id, component)
  WHERE is_current_usable = TRUE;

CREATE INDEX idx_cliente_snapshots_history
  ON cliente_snapshots (cliente_id, component, collected_at DESC, id DESC);

CREATE INDEX idx_cliente_snapshots_valid
  ON cliente_snapshots (cliente_id, component, valid_until)
  WHERE is_current_usable = TRUE;
```

Regras de materialização:

1. `ERROR` nunca recebe `is_current_usable = TRUE`.
2. `OK` pode substituir o vigente quando a validação do componente concluir.
3. `EMPTY` só substitui o vigente quando o componente declarar que vazio é
   autoritativo.
4. A troca deve ocorrer em função SQL que bloqueia o par
   `(cliente_id, component)` e mantém o índice parcial único.
5. O último resultado utilizável e a última tentativa são consultas distintas.
6. Snapshot vencido permanece histórico, mas não pode ser reutilizado.

### 6.4 Proveniência na operação

O snapshot operacional continua sendo a fotografia autocontida usada pelo
score. Quando vier do cadastro do cliente, recebe a referência da versão
consumida:

```sql
ALTER TABLE component_snapshots
  ADD COLUMN source_cliente_snapshot_id UUID
  REFERENCES cliente_snapshots(id) ON DELETE SET NULL;

CREATE INDEX idx_component_snapshots_cliente_source
  ON component_snapshots (source_cliente_snapshot_id)
  WHERE source_cliente_snapshot_id IS NOT NULL;
```

Em cache hit do cliente, o pipeline deve:

1. localizar um `cliente_snapshot` utilizável e não vencido;
2. copiar `raw_result` e `parsed_result` para `component_snapshots`;
3. preencher `source_cliente_snapshot_id`;
4. registrar `duration_ms = 0` e origem `CLIENTE_SNAPSHOT`;
5. nunca consultar novamente a versão corrente durante o mesmo score.

Essa cópia é proposital: mantém a operação reproduzível mesmo que o cadastro do
cliente seja atualizado ou eliminado por política de retenção.

### 6.5 `cliente_sinais_financeiros`

Cada linha representa uma medição financeira consolidada em uma data de corte.

```sql
CREATE TABLE cliente_sinais_financeiros (
  id                          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id                  UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source_snapshot_id          UUID NOT NULL
                              REFERENCES cliente_snapshots(id) ON DELETE RESTRICT,
  coletado_em                 TIMESTAMPTZ NOT NULL,
  competencia_ate             DATE,

  janela_12m_inicio           DATE,
  janela_12m_fim              DATE,
  faturamento_verificado_12m  NUMERIC(15,2),
  meses_com_recebimento       INTEGER,
  primeira_competencia        DATE,
  ultima_competencia          DATE,

  hhi                         NUMERIC(8,2),
  hhi_faixa                   VARCHAR(20),
  n_orgaos                    INTEGER,
  top_orgao                   TEXT,
  top_participacao            NUMERIC(7,6),

  cv_volatilidade             NUMERIC(8,6),
  maior_queda_anual_pct       NUMERIC(8,4),

  fonte_primaria              VARCHAR(30),
  reconciliacao_status        VARCHAR(30),
  reconciliacao_divergencia   NUMERIC(8,4),
  source_operation_id         UUID REFERENCES operations(id) ON DELETE SET NULL,

  created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT cliente_sinais_hhi_check
    CHECK (hhi IS NULL OR hhi BETWEEN 0 AND 10000),
  CONSTRAINT cliente_sinais_top_part_check
    CHECK (top_participacao IS NULL OR top_participacao BETWEEN 0 AND 1),
  CONSTRAINT cliente_sinais_meses_check
    CHECK (meses_com_recebimento IS NULL OR meses_com_recebimento >= 0),
  UNIQUE (source_snapshot_id)
);

CREATE INDEX idx_sinais_cliente_recente
  ON cliente_sinais_financeiros (cliente_id, coletado_em DESC, id DESC);

CREATE INDEX idx_sinais_hhi ON cliente_sinais_financeiros (hhi);
```

O vigente é o sinal cuja coleta utilizável é mais recente. Empate de timestamp é
resolvido por `id DESC`; timestamps não são usados para dedupe.

### 6.6 `cliente_faturamento_anual`

A série anual deve ser versionada pela coleta financeira. Não há upsert global
por cliente e ano.

```sql
CREATE TABLE cliente_faturamento_anual (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  sinal_id          UUID NOT NULL
                    REFERENCES cliente_sinais_financeiros(id) ON DELETE CASCADE,
  ano               INTEGER NOT NULL,
  valor_total       NUMERIC(15,2) NOT NULL,
  n_pagamentos      INTEGER,
  parcial           BOOLEAN NOT NULL DEFAULT FALSE,

  CONSTRAINT cliente_faturamento_ano_check CHECK (ano BETWEEN 2000 AND 2200),
  CONSTRAINT cliente_faturamento_valor_check CHECK (valor_total >= 0),
  CONSTRAINT cliente_faturamento_pagamentos_check
    CHECK (n_pagamentos IS NULL OR n_pagamentos >= 0),
  UNIQUE (sinal_id, ano)
);

CREATE INDEX idx_faturamento_anual_sinal
  ON cliente_faturamento_anual (sinal_id, ano);
```

Uma view pode expor apenas a série associada ao sinal financeiro vigente. As
versões anteriores permanecem disponíveis para explicar scores históricos.

### 6.7 Coletas e sanções

É necessário provar que a consulta de sanções percorreu integralmente a fonte
antes de marcar registros como ausentes.

```sql
CREATE TABLE cliente_sancao_coletas (
  id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id           UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source               VARCHAR(30) NOT NULL,
  collection_key       UUID NOT NULL,
  result_state         VARCHAR(10) NOT NULL,
  pagination_complete  BOOLEAN NOT NULL DEFAULT FALSE,
  started_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  completed_at         TIMESTAMPTZ,
  error_message        TEXT,

  CONSTRAINT sancao_coleta_state_check
    CHECK (result_state IN ('OK', 'EMPTY', 'ERROR')),
  UNIQUE (cliente_id, source, collection_key)
);

CREATE TABLE cliente_sancoes (
  id                   UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id           UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  source               VARCHAR(30) NOT NULL,
  tipo                 VARCHAR(20) NOT NULL,
  identificador        TEXT NOT NULL,

  orgao_sancionador    TEXT,
  descricao            TEXT,
  data_inicio          DATE,
  data_fim             DATE,

  primeira_deteccao    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ultima_confirmacao   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  ausente_desde        TIMESTAMPTZ,
  last_seen_run_id     UUID REFERENCES cliente_sancao_coletas(id),
  payload              JSONB,

  CONSTRAINT cliente_sancoes_datas_check
    CHECK (data_fim IS NULL OR data_inicio IS NULL OR data_fim >= data_inicio),
  UNIQUE (cliente_id, source, tipo, identificador)
);

CREATE INDEX idx_sancoes_cliente_observadas
  ON cliente_sancoes (cliente_id, tipo)
  WHERE ausente_desde IS NULL;
```

Regras:

1. `ausente_desde` só pode ser preenchido após coleta `OK` ou `EMPTY` com
   `pagination_complete = TRUE`.
2. A marcação de ausências e a conclusão da coleta ocorrem na mesma transação.
3. Coleta `ERROR` não altera sanções existentes.
4. Se uma sanção reaparecer, `ausente_desde` volta a `NULL` e
   `ultima_confirmacao` é atualizada.
5. “Observada na fonte” e “juridicamente vigente” são conceitos distintos. Uma
   view de sanções vigentes deve considerar também `data_inicio` e `data_fim`.

### 6.8 Pessoas e vínculos societários

Deduplicação por nome não é suficiente. CPF completo e nome mascarado podem
produzir registros duplicados; homônimos podem ser fundidos incorretamente. A
modelagem separa pessoa, vínculo e evidência.

```sql
CREATE TABLE pessoas (
  id                UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cpf               VARCHAR(11),
  cpf_mascarado     VARCHAR(20),
  nome              TEXT NOT NULL,
  nome_normalizado  TEXT NOT NULL,
  match_status      VARCHAR(20) NOT NULL DEFAULT 'UNRESOLVED',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT pessoas_match_status_check
    CHECK (match_status IN ('CONFIRMED', 'PROBABLE', 'UNRESOLVED', 'CONFLICT')),
  CONSTRAINT pessoas_cpf_check
    CHECK (cpf IS NULL OR cpf ~ '^[0-9]{11}$')
);

CREATE UNIQUE INDEX idx_pessoas_cpf
  ON pessoas (cpf)
  WHERE cpf IS NOT NULL;

CREATE INDEX idx_pessoas_nome_normalizado ON pessoas (nome_normalizado);

CREATE TABLE cliente_vinculos_societarios (
  id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  cliente_id         UUID NOT NULL REFERENCES clientes(id) ON DELETE CASCADE,
  pessoa_id          UUID REFERENCES pessoas(id) ON DELETE RESTRICT,
  nome_observado     TEXT NOT NULL,
  qualificacao       TEXT,
  data_entrada       DATE,
  participacao_pct   NUMERIC(5,2),
  is_administrador   BOOLEAN,
  detectado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  removido_em        TIMESTAMPTZ,
  ultima_confirmacao TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  CONSTRAINT vinculos_participacao_check
    CHECK (participacao_pct IS NULL OR participacao_pct BETWEEN 0 AND 100)
);

CREATE TABLE cliente_socio_evidencias (
  id                 UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  vinculo_id         UUID NOT NULL
                     REFERENCES cliente_vinculos_societarios(id) ON DELETE CASCADE,
  source_snapshot_id UUID REFERENCES cliente_snapshots(id) ON DELETE SET NULL,
  fonte              VARCHAR(30) NOT NULL,
  source_identifier  TEXT NOT NULL,
  payload            JSONB,
  observado_em       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

  UNIQUE (vinculo_id, fonte, source_identifier)
);
```

Regras de identidade:

- CPF completo confirmado permite associação determinística;
- CPF mascarado nunca é comparado como se fosse CPF completo;
- nome normalizado é pista, não chave única;
- associação nominal ambígua recebe `UNRESOLVED` ou `CONFLICT`;
- múltiplas fontes confirmam o mesmo vínculo por meio de evidências separadas;
- quando a fonte não fornecer identificador, a aplicação gera hash determinístico
  dos campos originais como `source_identifier`;
- saída e posterior retorno ao quadro geram novo período de vínculo ou reabrem o
  vínculo conforme regra explicitamente registrada.

`cpf` permanece nulo quando a fonte possuir somente CPF mascarado. O formato é
protegido pela constraint; os dígitos verificadores devem ser validados pela
aplicação antes da persistência.

CPF é dado pessoal. A criptografia em nível de campo fica adiada até o projeto
possuir gestão segura de chaves, rotação e recuperação. Essa decisão não remove
nem altera eventual criptografia de infraestrutura, disco ou backups. Os
controles uniformes de dado pessoal estão tratados nas seções 12 e 15.

### 6.9 Vínculo de operações com clientes

```sql
ALTER TABLE operations
  ADD COLUMN cliente_id UUID
  REFERENCES clientes(id) ON DELETE RESTRICT,
  ADD COLUMN cliente_cadastro_revision BIGINT;

CREATE INDEX idx_operations_cliente ON operations (cliente_id);
```

`cliente_cadastro_revision` registra a revisão cadastral vigente na criação da
operação. A reprodução dos componentes usa
`component_snapshots.source_cliente_snapshot_id`; os dois mecanismos são
complementares.

Na etapa expand, `cliente_id` é nulo para registros ainda não migrados. Depois do
backfill e da validação, deve se tornar `NOT NULL` para novas operações de pessoa
jurídica.

---

## 7. Limite aprovado e exposição

### 7.1 Limite aprovado por cliente

Enquanto não existe ciclo de desembolso e liquidação, a consulta deve se chamar
`cliente_limites_aprovados`, não `cliente_exposicao`.

```sql
CREATE VIEW cliente_limites_aprovados AS
SELECT
  c.id AS cliente_id,
  c.cnpj,
  c.cnpj_raiz,
  c.razao_social,
  COUNT(*) FILTER (WHERE o.status = 'approved') AS operacoes_aprovadas,
  COUNT(*) FILTER (
    WHERE o.status IN ('pending', 'processing', 'completed', 'escalated')
  ) AS operacoes_em_analise,
  COALESCE(SUM(o.valor_enquadrado) FILTER (
    WHERE o.status = 'approved'
  ), 0) AS limite_aprovado_acumulado,
  MAX(o.created_at) AS ultima_operacao_em
FROM clientes c
LEFT JOIN operations o ON o.cliente_id = c.id
GROUP BY c.id, c.cnpj, c.cnpj_raiz, c.razao_social;
```

### 7.2 Consolidação por raiz

Uma segunda view deve agregar `cliente_limites_aprovados` por `cnpj_raiz`. Isso
permite observar matriz e filiais em conjunto sem afirmar que a raiz representa
todo o grupo econômico.

### 7.3 Exposição financeira futura

Exposição financeira real exigirá, em frente própria:

- formalização;
- desembolso;
- amortizações;
- liquidação;
- cancelamento;
- saldo devedor atual.

Até essa frente existir, `valor_enquadrado` aprovado não deve ser apresentado
como “total cedido” ou “saldo exposto”.

### 7.4 Uso futuro na elegibilidade

Se limite aprovado ou exposição virar regra S0, a consulta e a reserva do limite
devem ocorrer atomicamente. Ler uma view e depois criar a operação permite que
duas operações concorrentes ultrapassem o limite.

---

## 8. Integração com o módulo documental W4

A W8 não altera a autoridade das regras documentais.

- documentos são entidades canônicas do módulo W4;
- abrangência é determinada pelo titular e pela validação do documento;
- certidão não é reaproveitada automaticamente apenas porque os CNPJs têm a
  mesma raiz;
- CND Federal, CNDT e FGTS seguem a abrangência extraída e validada;
- documentos de contrato ou cotação permanecem nesses escopos;
- projeções documentais consumidas pelo score continuam versionadas pelas
  revisões definidas na W4.

O `cnpj_raiz` é apenas uma chave de busca por candidatos. A decisão de
reaproveitamento continua pertencendo ao motor documental.

---

## 9. Cache e política de reutilização

### 9.1 Etapa 1

`cnpj_cache` continua funcionando sem alteração. `cliente_snapshots` recebe
dual-write apenas para observação e comparação.

Devem ser medidos:

- frequência de mudança por componente;
- taxa de resultados `OK`, `EMPTY` e `ERROR`;
- divergência entre cache atual e snapshot do cliente;
- quantidade de consultas potencialmente evitáveis;
- idade do dado no momento em que foi consumido.

### 9.2 Etapa 2

O pipeline consulta `cliente_snapshots` antes da fonte externa. Reuso exige:

```text
is_current_usable = TRUE
AND status = 'completed'
AND result_state IN ('OK', 'EMPTY')
AND (valid_until IS NULL OR valid_until > NOW())
```

Além disso, `EMPTY` só é reutilizável se o componente o considerar autoritativo.

### 9.3 Etapa 3

`cnpj_cache` só pode ser removido quando:

1. todos os consumidores tiverem migrado;
2. métricas mostrarem equivalência de comportamento;
3. não houver fallback silencioso para a tabela antiga;
4. rollback para o leitor anterior não for mais necessário.

---

## 10. Fluxos de escrita

### 10.1 Criação de operação

1. normalizar e validar o CNPJ;
2. fazer upsert idempotente do cliente sem substituir valores não nulos por
   nulos;
3. criar a operação com `cliente_id`, `cnpj` e `razao_social` da originação;
4. criar os snapshots operacionais;
5. confirmar tudo na mesma transação;
6. só então disparar o pipeline.

O fluxo deve ser implementado por RPC transacional ou mecanismo equivalente.
Uma falha depois de criar o cliente e antes de criar a operação deve ser
recuperável e idempotente.

### 10.2 Coleta de componente de cliente

1. criar ou reivindicar `collection_key`;
2. executar a fonte sem manter transação de banco aberta;
3. classificar o resultado como `OK`, `EMPTY` ou `ERROR`;
4. persistir o snapshot da tentativa;
5. validar se o resultado pode se tornar utilizável;
6. trocar o vigente atomicamente;
7. materializar campos e tabelas derivadas na mesma transação;
8. registrar métricas e auditoria.

### 10.3 Consumo por operação

1. selecionar snapshot válido antes de iniciar consulta externa;
2. copiar o resultado para o snapshot operacional;
3. gravar `source_cliente_snapshot_id`;
4. não reavaliar o snapshot selecionado durante a mesma execução;
5. se não houver snapshot reutilizável, consultar a fonte e produzir ambos os
   snapshots de forma idempotente.

---

## 11. Migração expand/contract

### Fase 0 — Preparação

1. adicionar `data_scope` ao catálogo de componentes;
2. classificar todos os componentes existentes;
3. criar extensions necessárias com `IF NOT EXISTS`;
4. criar tabelas, constraints, índices, triggers e RLS;
5. adicionar `cliente_id` e `source_cliente_snapshot_id` como nulos.

### Fase 1 — Expand e dual-write

1. fazer upsert de `clientes` na criação de novas operações;
2. preencher `operations.cliente_id` sem alterar leitores atuais;
3. dual-write somente componentes de escopo `CLIENTE`;
4. manter `cnpj_cache` e `component_snapshots` como hoje;
5. instrumentar divergências e falhas do dual-write;
6. nenhuma falha na estrutura nova pode derrubar o pipeline nesta fase.

### Fase 2 — Backfill

1. criar clientes distintos a partir dos CNPJs válidos em `operations`;
2. vincular operações em lotes idempotentes;
3. preservar `cnpj` e `razao_social` originais;
4. gerar snapshots de cliente apenas quando houver proveniência confiável;
5. não transformar snapshot operacional antigo em “vigente” sem validar idade;
6. produzir relatório de CNPJs inválidos, duplicidades e vínculos não resolvidos.

### Fase 3 — Leitura progressiva por componente

O rollout é independente por componente e usa uma feature flag com três
estados, desligada por padrão:

| Estado | Comportamento |
|---|---|
| `OFF` | Consulta somente a fonte externa e não reutiliza snapshot do cliente |
| `SHADOW` | Consulta cadastro e fonte, usa a fonte no pipeline e registra divergência |
| `ACTIVE` | Usa snapshot válido do cliente e consulta a fonte apenas como fallback |

Cada componente deve manter os seguintes metadados:

```text
reuse_mode
shadow_started_at
shadow_min_days
shadow_min_samples
shadow_sample_count
shadow_max_divergence_pct
```

Valores iniciais:

```text
shadow_min_days = 7
shadow_min_samples = 30
```

`shadow_max_divergence_pct` é configurável por componente. Componentes cujos
resultados não sejam comparáveis por percentual devem definir uma função de
equivalência determinística e uma métrica compatível.

A promoção automática de `SHADOW` para `ACTIVE` exige simultaneamente:

1. período mínimo atingido;
2. amostra mínima atingida;
3. divergência dentro da tolerância configurada;
4. nenhuma confusão entre `ERROR` e `EMPTY`;
5. nenhuma falha estrutural de materialização;
6. nenhum snapshot vencido consumido.

Quando todos os critérios forem satisfeitos, a promoção ocorre sem aprovação
humana. Caso contrário, o componente permanece em `SHADOW` e emite alerta
estruturado ao fim do período mínimo. Divergência em um componente não bloqueia
o rollout dos demais.

Em `ACTIVE`, o pipeline consulta a fonte quando o snapshot estiver ausente,
vencido, inválido, com `result_state = ERROR` ou incompatível com o escopo da
operação. Falha nesse fallback não pode apagar o último snapshot válido.

O rollback de `ACTIVE` para `OFF` pode ser realizado a qualquer momento e afeta
somente o componente selecionado.

### Fase 4 — Contract

1. tornar `cliente_id` obrigatório para novas operações PJ;
2. remover leitores do `cnpj_cache` somente após equivalência comprovada;
3. manter `operations.cnpj` e `operations.razao_social` permanentemente;
4. não remover snapshots operacionais usados por decisões históricas;
5. documentar procedimento de rollback e reparo de dual-write.

---

## 12. Segurança, RLS e retenção

Todas as tabelas novas devem ter RLS habilitado. O backend usa `service_role`;
acesso direto do frontend só deve existir por endpoints autenticados e com
controle de alçada.

Controles vigentes na implantação da W8:

- policy reexecutável para `service_role`;
- acesso às estruturas novas apenas pelo backend e por endpoints autenticados.

O CPF já trafega em texto na integração Broadfactor e pode estar presente em
`component_snapshots.raw_result`, `component_snapshots.parsed_result`, retornos
de componentes e logs existentes. Aplicar controles rigorosos somente à tabela
`pessoas` criaria uma proteção parcial sem reduzir a exposição sistêmica.

A W8 não piora a exposição atual; ela apenas materializa em estrutura própria
um dado que já trafega no sistema. Enquanto não existir uma frente transversal
de tratamento de dado pessoal, permanecem os controles atuais: RLS com policy
de `service_role` e acesso somente por endpoint autenticado.

Mascaramento em listagens, autorização e auditoria específicas para CPF
completo, exclusão de dados pessoais de logs, revisão dos payloads históricos e
privilégios de coluna devem ser implementados em frente própria. Essa frente
também deve definir retenção por categoria, legal hold e regras de exportação,
anonimização ou exclusão. O tratamento deve cobrir o sistema inteiro e não
apenas a W8.

O adiamento refere-se somente à criptografia em nível de campo. Eventual
criptografia de infraestrutura, disco, transporte, storage ou backups permanece
fora dessa decisão e não deve ser removida.

Triggers de histórico não substituem `audit_trail`: histórico preserva estado;
auditoria registra ator, ação, motivo e contexto.

---

## 13. Observabilidade

Eventos estruturados mínimos:

```text
client.created
client.linked_to_operation
client.profile_materialized
client.profile_conflict
client.snapshot_started
client.snapshot_completed
client.snapshot_empty
client.snapshot_failed
client.snapshot_promoted
client.snapshot_reused
client.snapshot_expired
client.dual_write_failed
client.shadow_divergence
client.sanctions_absence_applied
client.identity_match_conflict
```

Cada evento relevante deve registrar `cliente_id`, CNPJ mascarado quando
necessário, componente, `collection_key`, `source_operation_id`, estado e
duração.

Métricas mínimas:

- clientes criados e vinculados;
- operações sem `cliente_id`;
- snapshots por estado;
- taxa de reuso por componente;
- chamadas externas evitadas;
- snapshots vencidos consumidos, que deve ser zero;
- divergências de shadow read;
- conflitos de materialização cadastral;
- coletas de sanções incompletas;
- identidades societárias não resolvidas.

---

## 14. Critérios de aceite

### Cadastro

- [ ] Duas operações do mesmo CNPJ apontam para o mesmo `cliente_id`.
- [ ] Matriz e filial possuem `cliente_id` distintos e o mesmo `cnpj_raiz`.
- [ ] Retry da criação não duplica cliente nem operação.
- [ ] Valor nulo de uma fonte não apaga valor cadastral não nulo.
- [ ] Observação antiga não sobrescreve observação mais recente.
- [ ] Conflito entre valores não nulos não usa last-write-wins.
- [ ] Override manual com `locked = true` não é sobrescrito automaticamente.
- [ ] Atualizações concorrentes de `field_provenance` não perdem campos.
- [ ] Atualização sem mudança material não cria histórico.
- [ ] Mudança material incrementa `cadastro_revision` uma única vez.

### Identidade

- [ ] CPF com formato ou dígitos verificadores inválidos é rejeitado pela
  aplicação.
- [ ] CPF mascarado nunca é comparado como CPF completo.
- [ ] Índice único parcial impede duplicidade quando o CPF é conhecido.
- [ ] Associação ambígua resulta em `UNRESOLVED` ou `CONFLICT`, nunca em fusão
  silenciosa.

### Snapshots

- [ ] Nunca existem dois snapshots utilizáveis para o mesmo cliente e componente.
- [ ] Coleta `ERROR` não substitui o último snapshot utilizável.
- [ ] `EMPTY` só substitui quando configurado como autoritativo.
- [ ] Retry com a mesma `collection_key` não duplica a coleta.
- [ ] Operação registra o `source_cliente_snapshot_id` consumido.
- [ ] Reprocessamento da operação usa a versão vinculada, não a versão atual.

### Financeiro

- [ ] Janeiro de 2026 ordena depois de dezembro de 2025.
- [ ] Uma correção da série de 2025 não apaga a versão usada por score anterior.
- [ ] O sinal de 12 meses registra início e fim da janela.
- [ ] HHI permanece entre 0 e 10.000 e participação entre 0 e 1.

### Sanções

- [ ] Erro ou paginação incompleta não marca sanção como ausente.
- [ ] Coleta vazia completa pode marcar ausência.
- [ ] Sanção que reaparece limpa `ausente_desde`.
- [ ] Consulta de vigência considera `data_inicio` e `data_fim`.

### Operações e limites

- [ ] Operação preserva CNPJ e razão social observados na originação.
- [ ] Limites aprovados são consultáveis por CNPJ e por raiz.
- [ ] A interface não apresenta limite aprovado como saldo desembolsado.
- [ ] Nenhuma regra S0 usa exposição sem reserva atômica.

### Rollout

- [ ] Todo componente inicia em `OFF`.
- [ ] Em `SHADOW`, o pipeline usa a fonte e somente compara o cadastro.
- [ ] Promoção exige período e amostra mínimos.
- [ ] Componente com divergência permanece em `SHADOW`.
- [ ] Em `ACTIVE`, o pipeline usa cadastro válido e faz fallback corretamente.
- [ ] `ERROR` nunca é tratado como `EMPTY`.
- [ ] Rollback para `OFF` afeta somente o componente selecionado.

### Segurança

- [ ] Todas as tabelas novas têm RLS e policy de `service_role`.
- [ ] Estruturas novas são acessadas somente pelo backend e por endpoints
  autenticados.

---

## 15. Decisões de produto pendentes

### Bloqueantes antes de usar limites na elegibilidade

1. O limite será aplicado por CNPJ, por raiz ou simultaneamente nos dois níveis?
2. Qual evento transforma limite aprovado em exposição financeira?
3. Como reservas concorrentes de limite serão liberadas ou convertidas?

### Não bloqueantes para a fase expand

1. TTL por componente, a calibrar durante o estado `SHADOW`.
2. Retenção e anonimização de cliente inativo.
3. Modelagem de grupos econômicos entre raízes diferentes.
4. Entrada futura de tomador pessoa física.
5. Política completa de precedência cadastral por campo e fonte.
6. Critérios para liberar um override manual bloqueado.
7. Tratamento automático de conflitos recorrentes entre fontes.
8. Frente de tratamento uniforme de dado pessoal, cobrindo mascaramento,
   auditoria de acesso, exclusão de logs e revisão dos payloads já persistidos.
9. Reavaliar criptografia em nível de campo quando o projeto possuir gestão
   segura de chaves, rotação e procedimento de recuperação.

Enquanto essas decisões estiverem abertas, a W8 pode criar, popular e medir a
estrutura nova, mas não deve alterar automaticamente elegibilidade, limite ou
decisão de crédito.

---

## 16. Resultado esperado

Ao concluir a W8:

- cada operação PJ estará ligada a uma empresa canônica;
- a operação continuará sendo uma fotografia histórica;
- dados reutilizados terão versão e proveniência explícitas;
- falha de consulta não apagará evidência válida;
- séries financeiras serão temporalmente corretas e reprodutíveis;
- sanções e vínculos societários manterão histórico observável;
- limites aprovados poderão ser consolidados por CNPJ e raiz;
- o sistema estará preparado para reduzir chamadas externas sem introduzir
  reuso indevido ou aumentar intervenção humana.

---

## 17. Ajustes da etapa 1 (v3)

1. A etapa 1 fica dividida em 1a, com vínculo cliente-operação e log em
   `cliente_snapshots`, e 1b, com as projeções de materialização cadastral e
   `clientes_historico`, sinais financeiros, faturamento anual, sanções e
   pessoas/vínculos. As projeções são reconstruíveis a partir do log.
2. `clientes.cnpj` aceita CNPJ alfanumérico conforme o padrão
   `^[0-9A-Z]{12}[0-9]{2}$`.
3. Em `cliente_snapshots`, `collected_at` é fornecido pela aplicação; não há
   FK para `cotacoes_broadfactor`; foram adicionados
   `degradado`/`degradacao_motivo`, `fonte` e `payload_hash`; e `status` fica
   restrito a `completed`/`failed`. A RPC aceita `raw_result`, mas ele não é
   gravado quando for idêntico a `parsed_result`; rejeita
   `source_operation_id` de outro cliente; e permite `completed` + `ERROR`
   para registrar falha semântica silenciosa do pipeline.
4. A promoção do snapshot vigente rejeita observação com `collected_at`
   anterior ao da observação vigente.
5. `valid_until` corresponde a `collected_at +
   component_config.cache_ttl_hours`; TTL nulo ou zero nasce vencido.
6. Na etapa 1a, o vínculo cliente-operação ocorre após o insert e em regime
   best-effort. A criação transacional única descrita na seção 10.1 entra na
   fase contract.
7. `empty_result_authoritative` permanece `FALSE`. Antes da etapa 2 para
   listas de sanção, o fetcher do Portal precisa distinguir JSON `[]` de corpo
   vazio.
8. As views de limite aprovado entram depois do backfill, com
   `security_invoker = true` e a lista de status alinhada ao enum de produção.
9. As RPCs têm `EXECUTE` revogado de `PUBLIC`, `anon` e `authenticated`.
10. Operações de teste, incluindo `playwright_e2e`, `debug_e2e`,
    `*_smoke_test` e demais origens não produtivas, continuam gerando cliente
    e observações. Métricas de shadow, critérios de promoção e views de limite
    consideram somente operações de origem produtiva, filtrando por
    `operations.source` por meio de `source_operation_id`.
