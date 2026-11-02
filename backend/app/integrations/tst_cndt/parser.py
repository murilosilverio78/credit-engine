"""Deterministic parser for text extracted from a CNDT PDF."""

from __future__ import annotations

from datetime import date, datetime
import re
import unicodedata
from typing import Any


_DATE = r"(\d{2}/\d{2}/\d{4})"
_CNPJ = re.compile(r"\b(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}-?\d{2})\b")


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    return "".join(char for char in value if unicodedata.category(char) != "Mn").upper()


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(str(value), "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def _first(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        found = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if found:
            return found.group(1).strip()
    return None


def parse_cndt_text(text: str, expected_cnpj: str, *, today: date | None = None) -> dict[str, Any]:
    """Extract CNDT metadata without model inference or retaining PDF text.

    ``valida`` expresses issuer, CNPJ and expiration verification only; it is
    intentionally independent from a negative or positive debt outcome.
    """
    normalized = _plain(text)
    expected = digits(expected_cnpj)
    cnpj_match = _CNPJ.search(text)
    cnpj = digits(cnpj_match.group(1)) if cnpj_match else ""
    if "CERTIDAO POSITIVA COM EFEITOS DE NEGATIVA" in normalized:
        resultado = "positiva_com_efeitos_negativa"
    elif "CERTIDAO POSITIVA" in normalized:
        resultado = "positiva"
    elif "CERTIDAO NEGATIVA" in normalized and "DEBITOS TRABALHISTAS" in normalized:
        resultado = "negativa"
    else:
        resultado = None
    emissao = _first((
        rf"(?:EXPEDICAO|EMISSAO|EMITID[AO]\s+EM)\s*[:\-]?\s*{_DATE}",
    ), normalized)
    validade = _first((
        rf"VALIDADE\s*[:\-]?\s*{_DATE}",
    ), normalized)
    numero = _first((
        r"CERTIDAO\s*(?:N[Oº°.]?|NUMERO)\s*[:\-]?\s*([0-9./-]{4,80})",
        r"CODIGO\s+(?:DE\s+)?(?:CONTROLE|AUTENTICIDADE)\s*[:\-]?\s*([A-Z0-9./-]{4,80})",
    ), normalized)
    expiration = _date(validade)
    reference = today or date.today()
    issuer_ok = "TRIBUNAL SUPERIOR DO TRABALHO" in normalized and "DEBITOS TRABALHISTAS" in normalized
    cnpj_ok = bool(cnpj and cnpj == expected)
    return {
        "resultado": resultado,
        "data_emissao": emissao,
        "data_validade": validade,
        "numero_certidao": numero,
        "cnpj_certidao": cnpj or None,
        "orgao_emissor": "Tribunal Superior do Trabalho" if issuer_ok else None,
        "valida": bool(issuer_ok and cnpj_ok and expiration and expiration >= reference),
    }
