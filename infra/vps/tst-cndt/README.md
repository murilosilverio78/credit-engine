# Serviço CNDT no VPS brasileiro

Este serviço é o único lugar onde Chromium e `TWOCAPTCHA_API_KEY` podem existir.
Ele não recebe credenciais de R2 ou Supabase. A Railway transfere o PDF por TLS,
armazena-o em `clientes/<cnpj>/documentos/<sha256>.pdf`, registra lineage e cria
o snapshot. Sem URL/token configurados ou em qualquer falha, a esteira mantém o
upload manual existente, sem bloquear a análise.

## Instalação no Ubuntu 22.04

1. Atualize o checkout em `/opt/credit-engine`, crie o usuário e o diretório de
   estado:

   ```bash
   sudo useradd --system --home /var/lib/tst-cndt --shell /usr/sbin/nologin tstcndt
   sudo install -d -o tstcndt -g tstcndt -m 700 /var/lib/tst-cndt
   python3 -m venv /opt/tst-cndt-venv
   /opt/tst-cndt-venv/bin/pip install -r /opt/credit-engine/backend/requirements.txt
   /opt/tst-cndt-venv/bin/playwright install --with-deps chromium
   ```

   Se o instalador mostrar `needrestart`, escolha **Cancel** (ou não reinicie
   serviços) durante esta instalação controlada.

2. Crie o ambiente sem colocar segredo no histórico:

   ```bash
   sudo install -o root -g tstcndt -m 600 /dev/null /etc/tst-cndt.env
   sudoedit /etc/tst-cndt.env
   sudo chmod 600 /etc/tst-cndt.env
   ```

   Copie os nomes de `tst-cndt.env.example`, defina segredos reais e confirme
   que o arquivo pertence a `root:tstcndt`. Não use `export CHAVE=...` no shell.

3. Instale e valide o serviço localmente:

   ```bash
   sudo cp infra/vps/tst-cndt/tst-cndt.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now tst-cndt
   sudo systemctl status tst-cndt --no-pager
   curl -fsS http://127.0.0.1:8113/healthz
   ```

4. Acrescente `Caddyfile.cndt` ao Caddy já instalado, garanta que
   `CNDT_PROXY_TOKEN` esteja disponível para o processo do Caddy, valide e
   recarregue:

   ```bash
   sudo caddy validate --config /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```

5. Só depois de revisar e aplicar manualmente a migration `074`, configure na
   Railway `CNDT_PROXY_URL=https://certidoes.murilosilverio.ia.br`,
   `CNDT_PROXY_TOKEN` e, opcionalmente, `CNDT_PROXY_TIMEOUT_SECONDS=35` e
   `CNDT_CACHE_SAFETY_DAYS=7`. O timeout curto preserva a análise: se o VPS
   ainda estiver emitindo ou falhar, a tarefa manual CNDT é criada normalmente.

Não use stealth, spoofing, rotação de IP ou proxies residenciais. Se o TST
retornar bloqueio anti-bot, o coletor classifica `BLOQUEIO_ANTIBOT` e para.
O cache é reutilizado apenas até sete dias antes da validade impressa no PDF;
essa data prevalece sobre a expectativa de 180 dias.
