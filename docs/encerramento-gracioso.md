# Encerramento gracioso das análises

O backend registra em memória as análises iniciadas por `start_analysis`. Ao
receber o encerramento do lifespan do FastAPI, recusa novas operações e novas
ingestões e espera essas análises por até `SHUTDOWN_GRACE_SECONDS`, cujo padrão
é 90 segundos.

O Uvicorn é iniciado com `--timeout-graceful-shutdown 100`. Esse limite precisa
ser maior que `SHUTDOWN_GRACE_SECONDS` para que o lifespan tenha tempo de
concluir antes de o servidor cancelar as tarefas restantes.

A janela de drain configurada no Railway também precisa preservar o container
antigo por pelo menos 100 segundos após o sinal de encerramento. Se o Railway
interromper o processo antes desse prazo, o timeout do Uvicorn e a espera do
lifespan não conseguem proteger a análise; nesse caso, o watchdog assume a
recuperação na execução periódica seguinte.

Ao alterar `SHUTDOWN_GRACE_SECONDS`, mantenha a seguinte ordem:

1. janela de drain do Railway maior ou igual ao timeout do Uvicorn;
2. timeout do Uvicorn maior que `SHUTDOWN_GRACE_SECONDS`;
3. `SHUTDOWN_GRACE_SECONDS` suficiente para a duração normal das análises.
