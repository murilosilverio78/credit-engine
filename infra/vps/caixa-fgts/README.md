# PoC CRF FGTS no VPS brasileiro

Esta é uma PoC isolada: ela não integra a esteira de crédito, não recebe dados
da Railway e não deve ser exposta por Caddy/systemd nesta etapa. O objetivo é
validar manualmente o fluxo de navegador e o formato do certificado antes de
qualquer integração de produção.

## Ambiente

Use o mesmo venv, navegador instalado e caminho de browsers do `tst-cndt`.
Não instale Chromium em `/root/.cache`; o processo que executar a PoC precisa
ler `/opt/ms-playwright`.

```bash
export PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
cd /opt/credit-engine/backend
/opt/tst-cndt-venv/bin/python -m app.integrations.caixa_fgts --help
```

No mesmo arquivo de ambiente protegido já usado pelo serviço CNDT, inclua (ou
mantenha) somente:

```ini
TWOCAPTCHA_API_KEY=...
```

O arquivo deve continuar com proprietário `root:root` e modo `600`. Não inclua
a chave em comandos, logs, tickets ou no diretório de saída.

## Execução manual controlada

Quando houver autorização para um teste manual posterior, carregue o env de
forma apropriada ao operador e execute um único CNPJ por vez:

```bash
export PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
cd /opt/credit-engine/backend
/opt/tst-cndt-venv/bin/python -m app.integrations.caixa_fgts \
  --cnpj 04251091000199 --uf SP --out /var/lib/tst-cndt/caixa-fgts-output --debug
```

`--debug` grava HTML sanitizado e screenshots das etapas em `--out`; não grava
a chave 2captcha nem a resposta do captcha. O resultado JSON retorna `obtida`,
`irregular` (sem PDF e sem erro) ou `falha`. O cache é específico da CRF e só é
reutilizado até cinco dias antes do fim de validade; a taxa é de no máximo cinco
tentativas por minuto e há um lock por CNPJ.

## Limites operacionais

Não use stealth, spoofing, proxies residenciais, rotação de IP ou qualquer
tentativa de contornar proteção. Se houver HTTP 403, Imperva, ShieldSquare,
captcha interativo ou página de desafio, a execução encerra com
`BLOQUEIO_ANTIBOT` e um diagnóstico sanitizado (status, título e trecho HTML).
Não há retentativa para bloqueio anti-bot. Para captcha de imagem inválido há no
máximo duas tentativas totais.

O coletor usa o evento de download do navegador para PDFs — pois o corpo HTTP
pode vir vazio — e, se o portal renderizar o certificado como HTML, usa
`page.pdf()` para imprimir a página. Não crie unidade systemd, rota Caddy nem
variáveis de proxy para esta PoC sem uma etapa explícita de integração.
