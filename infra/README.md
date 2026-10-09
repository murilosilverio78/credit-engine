# Infraestrutura

## Proxy BR para PNCP

Quando o ambiente de execução estiver fora do Brasil, publique o PNCP pelo
mesmo VPS HostGator usado pelo proxy do Portal. O token é o mesmo segredo
configurado como `PORTAL_PROXY_TOKEN` no Caddy e deve ser definido no backend
como `PNCP_PROXY_TOKEN`.

```caddy
pncp.murilosilverio.ia.br {
    @sem_token not header X-Proxy-Token {$PORTAL_PROXY_TOKEN}
    respond @sem_token "unauthorized" 401

    reverse_proxy https://pncp.gov.br {
        header_up Host pncp.gov.br
    }
}
```

No backend, configure `PNCP_SEARCH_BASE_URL=https://pncp.murilosilverio.ia.br`
e `PNCP_PROXY_TOKEN` com o mesmo valor do segredo do proxy.
