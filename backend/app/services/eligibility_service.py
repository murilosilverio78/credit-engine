from dataclasses import dataclass
from typing import Optional


VALOR_MINIMO = 10_000.0
PRAZO_MINIMO_DIAS = 60
CNPJ_IDADE_MINIMA_MESES = 12
LIMITE_MAXIMO_PCT = 0.70


@dataclass
class EligibilityResult:
    elegivel: bool
    motivo: Optional[str] = None
    campo: Optional[str] = None


def check_eligibility(
    cnpj: str,
    valor_solicitado: Optional[float],
    contrato_saldo: Optional[float],
    prazo_dias: Optional[int],
) -> EligibilityResult:
    """
    Gate síncrono de elegibilidade. Verifica apenas os dados do input,
    sem consulta a APIs externas. Rápido e determinístico.

    Regras:
    1. Valor solicitado abaixo do mínimo operacional.
    2. Valor solicitado acima de 70% do saldo do contrato informado.
    3. Prazo informado abaixo do mínimo operacional.

    A verificação de CNPJ ativo e sanções permanece no pipeline completo.
    """
    if valor_solicitado is not None and valor_solicitado < VALOR_MINIMO:
        return EligibilityResult(
            elegivel=False,
            motivo=(
                f"Valor solicitado (R$ {valor_solicitado:,.2f}) abaixo do "
                f"mínimo operacional de R$ {VALOR_MINIMO:,.0f}."
            ),
            campo="valor_solicitado",
        )

    if contrato_saldo is not None and valor_solicitado is not None:
        limite_maximo = contrato_saldo * LIMITE_MAXIMO_PCT
        if valor_solicitado > limite_maximo:
            return EligibilityResult(
                elegivel=False,
                motivo=(
                    f"Valor solicitado (R$ {valor_solicitado:,.2f}) excede o "
                    f"limite máximo de 70% do saldo do contrato "
                    f"(R$ {limite_maximo:,.2f})."
                ),
                campo="valor_solicitado",
            )

    if prazo_dias is not None and prazo_dias < PRAZO_MINIMO_DIAS:
        return EligibilityResult(
            elegivel=False,
            motivo=(
                f"Prazo de {prazo_dias} dias abaixo do mínimo operacional de "
                f"{PRAZO_MINIMO_DIAS} dias."
            ),
            campo="prazo_dias",
        )

    return EligibilityResult(elegivel=True)
