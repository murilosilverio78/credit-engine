# Serviço CNDT no VPS brasileiro

Este serviço é o único lugar onde Chromium e `TWOCAPTCHA_API_KEY` podem existir.
Ele não recebe credenciais de R2 ou Supabase. A Railway transfere o PDF por TLS,
armazena-o em `clientes/<cnpj>/documentos/<sha256>.pdf`, registra lineage e cria
o snapshot. Sem URL/token configurados ou em qualquer falha, a esteira mantém o
upload manual existente, sem bloquear a análise.

## Instalação no Ubuntu 22.04 (como root)

1. Código, usuário, diretório de estado e navegador. O Chromium precisa ficar
   num caminho legível pelo usuário do serviço (não em `/root/.cache`), por isso
   o `PLAYWRIGHT_BROWSERS_PATH`. Use só as dependências mínimas do serviço
   (`requirements-vps.txt`), não as do backend inteiro:

   ```bash
   apt-get update && apt-get install -y git python3-venv python3-pip
   git clone --depth 1 https://github.com/murilosilverio78/credit-engine.git /opt/credit-engine
   useradd --system --home /var/lib/tst-cndt --shell /usr/sbin/nologin tstcndt
   install -d -o tstcndt -g tstcndt -m 700 /var/lib/tst-cndt
   python3 -m venv /opt/tst-cndt-venv
   /opt/tst-cndt-venv/bin/pip install -r /opt/credit-engine/backend/requirements-vps.txt
   export PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright
   /opt/tst-cndt-venv/bin/playwright install --with-deps chromium
   chmod -R a+rX /opt/ms-playwright
   ```

   Se o instalador mostrar `needrestart`, escolha **Cancel** (ou não reinicie
   serviços) durante esta instalação controlada.

2. Gere o token do serviço e crie o ambiente sem colocar segredo no histórico.
   O token vale para o serviço e para a variável `CNDT_PROXY_TOKEN` da Railway:

   ```bash
   openssl rand -hex 32          # copie este valor para a Railway; não o cole em chats
   install -o root -g root -m 600 /dev/null /etc/tst-cndt.env
   nano /etc/tst-cndt.env        # use os nomes de tst-cndt.env.example
   chmod 600 /etc/tst-cndt.env
   ```

   O systemd lê o arquivo como root, então `root:root` com modo 600 basta.
   Não use `export CHAVE=...` no shell.

3. Instale e valide o serviço localmente:

   ```bash
   cp /opt/credit-engine/infra/vps/tst-cndt/tst-cndt.service /etc/systemd/system/
   systemctl daemon-reload
   systemctl enable --now tst-cndt
   systemctl status tst-cndt --no-pager
   curl -fsS http://127.0.0.1:8113/healthz
   ```

4. Acrescente `Caddyfile.cndt` ao Caddy já instalado (o serviço faz a
   autenticação), crie o registro DNS do subdomínio apontando para o VPS, valide
   e recarregue:

   ```bash
   caddy validate --config /etc/caddy/Caddyfile
   systemctl reload caddy
   ```

5. Só depois de revisar e aplicar manualmente a migration `074`, configure na
   Railway `CNDT_PROXY_URL=https://certidoes.murilosilverio.ia.br`,
   `CNDT_PROXY_TOKEN` (o mesmo valor do passo 2) e, opcionalmente,
   `CNDT_PROXY_TIMEOUT_SECONDS=35` e `CNDT_CACHE_SAFETY_DAYS=7`. O timeout curto
   preserva a análise: se o VPS ainda estiver emitindo ou falhar, a tarefa manual
   CNDT é criada normalmente.

Não use stealth, spoofing, rotação de IP ou proxies residenciais. Se o TST
retornar bloqueio anti-bot, o coletor classifica `BLOQUEIO_ANTIBOT` e para.
O cache é reutilizado apenas até sete dias antes da validade impressa no PDF;
essa data prevalece sobre a expectativa de 180 dias.

## Atualizar o serviço

```bash
cd /opt/credit-engine && git pull && systemctl restart tst-cndt
```
