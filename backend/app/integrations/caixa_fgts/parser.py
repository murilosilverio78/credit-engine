"""Deterministic parser for CRF result pages and the printable certificate."""

from __future__ import annotations

from datetime import date, datetime
from html import unescape
import re
import unicodedata
from typing import Any


_DATE = r"(\d{2}/\d{2}/\d{4})"
_CNPJ = re.compile(r"\b(\d{2}[.\s]?\d{3}[.\s]?\d{3}[/\s]?\d{4}-?\d{2})\b")


def digits(value: object) -> str:
    return re.sub(r"\D", "", str(value or ""))


def _plain(value: str) -> str:
    value = unicodedata.normalize("NFD", unescape(value or ""))
    return "".join(character for character in value if unicodedata.category(character) != "Mn").upper()


def html_to_text(html: str) -> str:
    """Enough HTML normalization for stable fixtures without a browser dependency."""
    without_scripts = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", html or "", flags=re.I | re.S)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", without_scripts))).strip()


def _first(patterns: tuple[str, ...], text: str) -> str | None:
    for pattern in patterns:
        found = re.search(pattern, text, flags=re.I | re.S)
        if found:
            return re.sub(r"\s+", " ", found.group(1)).strip(" :-\u2013")
    return None


def _date(value: str | None) -> date | None:
    try:
        return datetime.strptime(str(value), "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def parse_crf_text(text: str, expected_cnpj: str, *, today: date | None = None) -> dict[str, Any]:
    """Parse fields exposed by the result view or printed CRF, without inference."""
    source = html_to_text(text)
    normalized = _plain(source)
    expected = digits(expected_cnpj)
    match = _CNPJ.search(source)
    cnpj = digits(match.group(1)) if match else ""
    irregular = bool(re.search(r"\bIRREGULAR(?:IDADE)?\b|NAO\s+REGULAR", normalized))
    regular = bool(re.search(r"\bREGULAR\b", normalized)) and not irregular
    situacao = "irregular" if irregular else "regular" if regular else None
    inicio = _first((
        rf"(?:INICIO\s+DA\s+VALIDADE|VALIDADE\s+DE|VALIDA\s+A\s+PARTIR\s+DE)\s*[:\-]?\s*{_DATE}",
        rf"VALIDADE\s*[:\-]?\s*{_DATE}\s*(?:A|ATE)",
    ), normalized)
    fim = _first((
        rf"(?:FIM\s+DA\s+VALIDADE|VALIDO\s+ATE|VALIDADE\s+ATE)\s*[:\-]?\s*{_DATE}",
        rf"VALIDADE\s*[:\-]?\s*{_DATE}\s*(?:A|ATE)\s*{_DATE}",
    ), normalized)
    if fim and inicio and fim == inicio:
        range_dates = re.search(rf"VALIDADE\s*[:\-]?\s*{_DATE}\s*(?:A|ATE)\s*{_DATE}", normalized)
        fim = range_dates.group(2) if range_dates else fim
    emissao = _first((rf"(?:DATA\s+DE\s+)?EMISSAO\s*[:\-]?\s*{_DATE}",), normalized)
    numero = _first((
        r"(?:NUMERO\s+(?:DO\s+)?(?:CERTIFICADO|CRF)|CERTIFICADO\s+(?:DE\s+REGULARIDADE\s+DO\s+FGTS\s*)?(?:N[Oº°.]?))\s*[:\-]?\s*([A-Z0-9./-]{4,80})",
    ), normalized)
    razao = _first((
        r"(?:RAZAO\s+SOCIAL|NOME\s+(?:EMPRESARIAL|DO\s+EMPREGADOR))\s*[:\-]?\s*([A-Z0-9 .&'/-]{2,160})(?=\s+(?:CNPJ|INSCRICAO|SITUACAO|NUMERO|DATA|VALIDADE)\b|$)",
    ), normalized)
    return {
        "situacao": situacao,
        "numero_certificado": numero,
        "data_emissao": emissao,
        "data_inicio_validade": inicio,
        "data_fim_validade": fim,
        "cnpj_certidao": cnpj or None,
        "razao_social": razao,
        "valida": bool(
            situacao == "regular" and cnpj == expected and _date(fim) and _date(fim) >= (today or date.today())
        ),
    }


def parse_crf_html(html: str, expected_cnpj: str, *, today: date | None = None) -> dict[str, Any]:
    return parse_crf_text(html_to_text(html), expected_cnpj, today=today)


def is_invalid_captcha(html: str) -> bool:
    plain = _plain(html_to_text(html))
    return "CAPTCHA" in plain and any(marker in plain for marker in ("INVALID", "INCORRET", "NAO CONFERE"))
