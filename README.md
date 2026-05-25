# AntecipaGov Credit Engine

Esteira automatizada de análise de crédito PJ para antecipação de recebíveis de contratos administrativos via Portal AntecipaGov (IN SEGES/MGI nº 82/2025).

## Arquitetura

```
Vercel          → Admin UI (Next.js) + Frontend MVP
Railway         → FastAPI + Celery workers
Supabase        → PostgreSQL
Upstash         → Redis (filas e cache)
Cloudflare R2   → PDFs e certidões
```

## Estrutura do repositório

```
antecipagov-credit-engine/
├── backend/                  # FastAPI + Celery workers (Railway)
│   ├── app/
│   │   ├── api/v1/endpoints/ # Rotas REST
│   │   ├── core/             # Config, settings, database
│   │   ├── models/           # SQLAlchemy models
│   │   ├── schemas/          # Pydantic schemas
│   │   ├── services/         # Lógica de negócio
│   │   ├── workers/          # Celery tasks (componentes de consulta)
│   │   └── utils/            # Helpers
│   └── tests/
├── frontend/                 # Next.js Admin UI (Vercel)
│   └── src/
│       ├── app/              # App Router
│       ├── components/       # UI components
│       └── lib/              # Utilities
├── infra/
│   ├── supabase/
│   │   └── migrations/       # SQL migrations versionadas
│   └── docker/               # Dockerfiles
└── docs/                     # Documentação técnica
```

## Setup rápido

### Pré-requisitos
- Python 3.12+
- Node.js 20+
- Contas: Supabase, Railway, Upstash, Cloudflare R2, Vercel

### Backend

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Preencher variáveis no .env
uvicorn app.main:app --reload
```

### Celery worker

```bash
cd backend
celery -A app.workers.celery_app worker --loglevel=info
```

### Frontend

```bash
cd frontend
npm install
cp .env.local.example .env.local
npm run dev
```

## Variáveis de ambiente

Ver `backend/.env.example` e `frontend/.env.local.example`.

## Componentes de consulta

Cada componente é um Celery worker independente:

| Componente | Tipo | Status |
|---|---|---|
| BrasilAPI | Automatizado | ✅ |
| Portal Transparência | Automatizado | ✅ |
| CNDT TST | Automatizado (2captcha) | ✅ |
| CND Federal | Human-in-loop | ⏳ upload manual |
| FGTS | Human-in-loop | ⏳ upload manual |
| Serasa PJ | Bureau externo | 🔜 roadmap |
| Boa Vista SCPC | Bureau externo | 🔜 roadmap |
| SERPRO | Bureau externo | 🔜 roadmap |
| Web Research | LLM (Claude Sonnet) | ✅ |
| Score Engine | LLM (Claude Opus) | ✅ |

## Scorecard 5D

| Dimensão | Peso |
|---|---|
| Saúde Cadastral | 25% |
| Regularidade Fiscal / Sanções | 35% |
| Relacionamento Governamental | 15% |
| Reputação / Mercado | 15% |
| Porte / Operacionalidade | 10% |

Rating: A (≥85) · B (70–84) · C (55–69) · D (40–54) · E (<40)
