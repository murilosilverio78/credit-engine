# PoC CNDT / TST

Módulo autônomo: não toca em banco, R2, achados, score, política ou migrations.
Ele usa Chromium headless e o endpoint público do TST; não usa stealth, spoofing,
proxies, rotação de IP ou qualquer tentativa de contornar bloqueio anti-bot.

## Ubuntu 22.04

```bash
cd /caminho/credit-engine/backend
python3 -m venv .venv && . .venv/bin/activate
pip install playwright requests pypdf
playwright install --with-deps chromium
read -s -p 'TWOCAPTCHA_API_KEY: ' TWOCAPTCHA_API_KEY; echo
export TWOCAPTCHA_API_KEY
python -m app.integrations.tst_cndt --cnpj 04251091000199 --saida /tmp/cndt
unset TWOCAPTCHA_API_KEY
```

`read -s` evita gravar a chave no histórico. Não use `export CHAVE=...` no
prompt, não coloque a chave em arquivos e não redirecione logs para locais
compartilhados. O teste acima é a única emissão real autorizada pelo operador.

Em sucesso, o JSON traz o mesmo conjunto de metadados do snapshot manual e
`storage_key` é somente o caminho absoluto local do PDF nesta fase. `valida`
significa emissor, CNPJ e vigência conferidos; não significa resultado negativo.
O resultado pode ser `negativa`, `positiva` ou `positiva_com_efeitos_negativa`.
Em falha, nenhum desses resultados é devolvido. A validade impressa no PDF
prevalece sobre a expectativa de 180 dias.

Falhas possíveis: `CAPTCHA_NAO_RESOLVIDO`, `PORTAL_INDISPONIVEL`,
`BLOQUEIO_ANTIBOT`, `FORMULARIO_ALTERADO`, `PDF_NAO_GERADO`, `CNPJ_INVALIDO`.
Há no máximo três compras de captcha por execução, cinco tentativas globais por
minuto no mesmo diretório de saída, lock por CNPJ e cache local: uma emissão
bem-sucedida por CNPJ/dia, reaproveitada apenas enquanto o PDF local existir e
a validade persistida ainda estiver vigente.

## Proposta para a próxima fase (não implementada)

Rodar este módulo em um serviço estreito no VPS, atrás do Caddy existente e do
header `X-Proxy-Token`; a Railway chamaria apenas esse endpoint autenticado. O
serviço gravaria o PDF no R2 e retornaria o JSON acima para materializar
`cndt_tst`. Antes de cada coleta, reaproveitar a certidão se a validade impressa
ainda estiver futura; agendar recoleta perto do vencimento e nunca usar a
situação negativa/positiva como atalho para vigência ou autenticidade.
