# Guardrails do Portal da Transparencia

O contador diario e persistido no Supabase pela migration
`035_portal_daily_guardrail.sql`. Cada chamada e reservada atomicamente antes
do HTTP, portanto deploys e reinicios do container nao zeram o consumo do dia.
O dia segue `America/Sao_Paulo`.

As janelas por minuto e por ciclo ficam em memoria e sao compartilhadas entre
todas as threads do processo. Essa implementacao pressupoe uma replica no
Railway. Antes de aumentar o numero de replicas, esses dois contadores precisam
ser movidos para coordenacao externa (por exemplo, Redis ou uma funcao atomica
no Postgres), pois cada processo teria sua propria janela.

Somente chamadas ao Portal da Transparencia consomem esses limites. Consultas
ao Comprasnet usam o mesmo helper de retry, mas ficam fora dos contadores do
Portal.
