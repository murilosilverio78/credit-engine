from dataclasses import dataclass, field
from typing import Optional

import structlog


# Broadfactor exposes margem_disponivel as exactly 70% of the gross remaining
# contract balance. If that external rule changes, this derivation must change.
PCT_MARGEM_SOBRE_SALDO = 0.70
logger = structlog.get_logger()


@dataclass
class EligibilityResult:
    elegivel: bool
    motivo: Optional[str] = None
    campo: Optional[str] = None
    valor_enquadrado: Optional[float] = None
    saldo_vincendo: Optional[float] = None
    prazo_final_meses: Optional[int] = None
    flags: list[str] = field(default_factory=list)


def check_eligibility(
    cnpj: str,
    valor_solicitado: Optional[float],
    contrato_saldo: Optional[float] = None,
    margem_disponivel: Optional[float] = None,
    prazo_dias: Optional[int] = None,
    prazo_vincendo_meses: Optional[int] = None,
    params: Optional[dict[str, float]] = None,
) -> EligibilityResult:
    """Aplica enquadramento antes das consultas pagas do pipeline."""
    if params is None:
        from app.services.eligibility_params_service import get_eligibility_config

        params = get_eligibility_config()

    ticket_minimo = float(params["ticket_minimo"])
    ticket_maximo = float(params["ticket_maximo"])
    pct_max_contrato = float(params["pct_max_contrato"])
    prazo_padrao_meses = int(params["prazo_padrao_meses"])
    prazo_minimo_dias = int(params["prazo_minimo_dias"])

    flags: list[str] = []
    if margem_disponivel is not None:
        saldo_vincendo = round(
            float(margem_disponivel) / PCT_MARGEM_SOBRE_SALDO,
            2,
        )
        flags.append("saldo_vincendo_derivado")
        if contrato_saldo is not None:
            logger.warning(
                "eligibility.both_margin_sources",
                cnpj=cnpj,
                margem_disponivel=margem_disponivel,
                contrato_saldo=contrato_saldo,
            )
            flags.append("margem_disponivel_prevalece_sobre_contrato_saldo")
    elif contrato_saldo is not None:
        saldo_vincendo = float(contrato_saldo)
    else:
        saldo_vincendo = None

    valor_enquadrado = None
    if valor_solicitado is not None:
        valor_enquadrado = float(valor_solicitado)
        if saldo_vincendo is not None:
            valor_enquadrado = min(
                valor_enquadrado,
                round(saldo_vincendo * pct_max_contrato, 2),
            )
        valor_enquadrado = round(valor_enquadrado, 2)

    if prazo_vincendo_meses is None:
        prazo_final_meses = prazo_padrao_meses
        flags.append("prazo_vincendo_indisponivel")
    else:
        prazo_final_meses = min(prazo_padrao_meses, int(prazo_vincendo_meses))

    result_data = {
        "valor_enquadrado": valor_enquadrado,
        "saldo_vincendo": saldo_vincendo,
        "prazo_final_meses": prazo_final_meses,
        "flags": flags,
    }

    if valor_enquadrado is not None and valor_enquadrado < ticket_minimo:
        return EligibilityResult(
            elegivel=False,
            motivo=(
                f"Valor enquadrado (R$ {valor_enquadrado:,.2f}) abaixo do "
                f"ticket minimo de R$ {ticket_minimo:,.2f}."
            ),
            campo="valor_enquadrado",
            **result_data,
        )

    if valor_enquadrado is not None and valor_enquadrado > ticket_maximo:
        return EligibilityResult(
            elegivel=False,
            motivo=(
                f"Valor enquadrado (R$ {valor_enquadrado:,.2f}) acima do "
                f"ticket maximo de R$ {ticket_maximo:,.2f}."
            ),
            campo="valor_enquadrado",
            **result_data,
        )

    if prazo_dias is not None and prazo_dias < prazo_minimo_dias:
        return EligibilityResult(
            elegivel=False,
            motivo=(
                f"Prazo de {prazo_dias} dias abaixo do minimo operacional de "
                f"{prazo_minimo_dias} dias."
            ),
            campo="prazo_dias",
            **result_data,
        )

    return EligibilityResult(elegivel=True, **result_data)
