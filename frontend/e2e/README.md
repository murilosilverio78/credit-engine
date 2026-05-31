# Playwright E2E

## Configuracao

1. Copie `e2e/.env.example` para `e2e/.env`.
2. Preencha `E2E_BASE_URL`, `E2E_API_URL` e as credenciais dos usuarios `diretor`, `analista` e `gerente`.
3. Os tres usuarios precisam existir no backend e estar com email confirmado.

Os testes usam `data-testid` estaveis e cacheiam login em `e2e/.auth/`.

## Rodando

```bash
npm run test:e2e
npm run test:e2e:headed
npm run test:e2e:ui
```

Alguns testes serao marcados com `@slow` porque aguardam o pipeline de analise e podem levar varios minutos.

## Certidoes

Testes do modulo de certidoes precisam de um PDF de exemplo em:

```text
frontend/e2e/fixtures/certidao-exemplo.pdf
```

Esse arquivo deve ser fornecido localmente e nao deve conter dados sensiveis.
