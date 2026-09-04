import structlog


logger = structlog.get_logger()


def record_ingestion_discard(
    *,
    cotacao_id: str | None,
    cnpj: str,
    valor_solicitado: float | None,
    margem_disponivel: float | None,
    valor_enquadrado: float | None,
    motivo: str,
    estagio: str,
) -> None:
    from app.core.database import supabase

    data = {
        "cotacao_id": cotacao_id,
        "cnpj": cnpj,
        "valor_solicitado": valor_solicitado,
        "margem_disponivel": margem_disponivel,
        "valor_enquadrado": valor_enquadrado,
        "motivo": motivo,
        "estagio": estagio,
    }
    supabase.table("descartes_ingestao").insert(data).execute()
    logger.info(
        "ingestion.discarded",
        cotacao_id=cotacao_id,
        cnpj=cnpj,
        valor_enquadrado=valor_enquadrado,
        motivo=motivo,
        estagio=estagio,
    )
