"""Extracao financeira preserva dados minimizados e nao emite achados."""

from __future__ import annotations
import hashlib
import re
from calendar import monthrange
from datetime import date
from typing import Any
import anthropic
import structlog
from app.core.config import settings
from app.core.database import supabase
from app.services.pdf_texto import (
    Deadline,
    OCRUnavailableError,
    extract_pdf_pages,
    normalize_document,
    ocr_pdf,
    render_page,
)

logger = structlog.get_logger()
SCHEMA_VERSAO, MAX_ATTEMPTS, GLOBAL_TIMEOUT_SECONDS = 2, 3, 840
FAMILIAS = (
    "SPED_ECD",
    "BALANCO_DRE",
    "BALANCETE",
    "BALANCO_ABERTURA",
    "FATURAMENTO",
    "ENDIVIDAMENTO_DECLARADO",
    "SCR",
    "INDICES",
    "IRPF",
    "CONTRATO_SOCIAL",
    "ECF",
    "OUTRO",
)
DERIVADAS = {"INDICES", "OUTRO"}
FORBIDDEN_KEYS = {"tipo", "tipo_documento", "document_type", "cpf"}
BALANCE = (
    "ativo_circulante",
    "disponibilidades",
    "contas_receber",
    "ativo_nao_circulante",
    "ativo_total",
    "passivo_circulante",
    "emprestimos_cp",
    "fornecedores",
    "obrigacoes_trabalhistas",
    "obrigacoes_tributarias",
    "passivo_nao_circulante",
    "emprestimos_lp",
    "patrimonio_liquido",
    "capital_social",
    "receita_bruta",
    "deducoes",
    "receita_liquida",
    "custos",
    "despesas_operacionais",
    "despesas_financeiras",
    "resultado_liquido",
)
PATRIMONIAL_FIELDS = (
    "ativo_circulante",
    "disponibilidades",
    "contas_receber",
    "ativo_nao_circulante",
    "ativo_total",
    "passivo_circulante",
    "emprestimos_cp",
    "fornecedores",
    "obrigacoes_trabalhistas",
    "obrigacoes_tributarias",
    "passivo_nao_circulante",
    "emprestimos_lp",
    "patrimonio_liquido",
    "capital_social",
)
RESULT_FIELDS = (
    "receita_bruta",
    "deducoes",
    "receita_liquida",
    "custos",
    "despesas_operacionais",
    "despesas_financeiras",
    "resultado_liquido",
)
KEYWORDS = {
    "SPED_ECD": ("ativo", "passivo", "patrimônio líquido", "demonstração do resultado"),
    "BALANCO_DRE": (
        "ativo",
        "passivo",
        "patrimônio líquido",
        "demonstração do resultado",
    ),
    "BALANCETE": ("ativo", "passivo", "balancete"),
    "BALANCO_ABERTURA": ("ativo", "passivo", "abertura"),
    "FATURAMENTO": ("faturamento", "receita", "competência"),
    "ENDIVIDAMENTO_DECLARADO": ("saldo devedor", "parcela", "credor", "cessão"),
    "SCR": ("carteira a vencer", "vencido", "limite"),
    "IRPF": ("bens e direitos", "dívidas e ônus", "rendimentos"),
    "CONTRATO_SOCIAL": ("capital social", "sócio", "administração"),
    "ECF": ("escrituracao contabil fiscal", "ecf", "recibo"),
}


def _rows(result: Any) -> list[dict[str, Any]]:
    return list(getattr(result, "data", None) or []) if result else []


def _single(result: Any) -> dict[str, Any] | None:
    return getattr(result, "data", None) if result else None


def _obj(
    properties: dict[str, Any], required: tuple[str, ...] | list[str]
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _ev() -> dict[str, Any]:
    return _obj(
        {
            "valor": {"type": ["number", "string", "null"]},
            "unidade": {"type": ["string", "null"]},
            "pagina": {"type": ["integer", "null"]},
            "trecho": {"type": ["string", "null"], "maxLength": 200},
            "confianca": {"type": "string", "enum": ["ALTA", "MEDIA", "BAIXA"]},
            "ausente_no_documento": {"type": "boolean"},
        },
        ("valor", "unidade", "pagina", "trecho", "confianca", "ausente_no_documento"),
    )


def _classification_tool() -> dict[str, Any]:
    p = {
        "familia": {"type": "string", "enum": list(FAMILIAS)},
        "ano_referencia": {"type": ["integer", "null"]},
        "titular_tipo": {"type": "string", "enum": ["PJ", "PF", "NAO_IDENTIFICADO"]},
        "tem_recibo_rfb": {"type": "boolean"},
        "assinado_contador": {"type": "boolean"},
        "crc": {"type": ["string", "null"]},
        "paginas_relevantes": {"type": "array", "items": {"type": "integer"}},
    }
    return {
        "name": "classificar_documento_financeiro",
        "input_schema": _obj(p, tuple(p)),
    }


def _extraction_tool(familia: str) -> dict[str, Any]:
    e = _ev()
    if familia in {"SPED_ECD", "BALANCO_DRE", "BALANCETE", "BALANCO_ABERTURA"}:
        ex = _obj(
            {
                "data_base": e,
                "natureza": {
                    "type": "string",
                    "enum": ["anual", "intermediario", "abertura"],
                },
                **{x: e for x in BALANCE},
            },
            ("data_base", "natureza", *BALANCE),
        )
        data = _obj({"exercicios": {"type": "array", "items": ex}}, ("exercicios",))
    elif familia == "FATURAMENTO":
        mes = _obj({"competencia": e, "valor": e}, ("competencia", "valor"))
        data = _obj(
            {
                "meses": {"type": "array", "items": mes},
                "total_declarado": e,
                "assinado_contador": {"type": "boolean"},
                "data_emissao": e,
            },
            ("meses", "total_declarado", "assinado_contador", "data_emissao"),
        )
    elif familia == "ENDIVIDAMENTO_DECLARADO":
        c = _obj(
            {
                "instituicao": {"type": ["string", "null"]},
                "modalidade": {"type": ["string", "null"]},
                "saldo_devedor": e,
                "parcela_mensal": e,
                "vencimento_final": e,
                "garantia": {"type": ["string", "null"]},
                "cessao_recebiveis": {"type": "boolean"},
                "contrato_cedido_referencia": {"type": ["string", "null"]},
            },
            (
                "instituicao",
                "modalidade",
                "saldo_devedor",
                "parcela_mensal",
                "vencimento_final",
                "garantia",
                "cessao_recebiveis",
                "contrato_cedido_referencia",
            ),
        )
        data = _obj(
            {
                "data_base": e,
                "credores": {"type": "array", "items": c},
                "total_declarado": e,
            },
            ("data_base", "credores", "total_declarado"),
        )
    elif familia == "SCR":
        m = _obj(
            {"modalidade": {"type": ["string", "null"]}, "valor": e},
            ("modalidade", "valor"),
        )
        data = _obj(
            {
                "data_base": e,
                "carteira_a_vencer": e,
                "vencido": e,
                "prejuizo": e,
                "limites_credito": e,
                "coobrigacoes": e,
                "modalidades": {"type": "array", "items": m},
                "instituicoes_count": e,
            },
            (
                "data_base",
                "carteira_a_vencer",
                "vencido",
                "prejuizo",
                "limites_credito",
                "coobrigacoes",
                "modalidades",
                "instituicoes_count",
            ),
        )
    elif familia == "IRPF":
        part = _obj(
            {"cnpj": {"type": ["string", "null"]}, "valor": e}, ("cnpj", "valor")
        )
        bens = _obj(
            {
                x: e
                for x in (
                    "imoveis",
                    "veiculos",
                    "participacoes_societarias",
                    "aplicacoes_depositos",
                    "especie",
                    "outros",
                )
            },
            (
                "imoveis",
                "veiculos",
                "participacoes_societarias",
                "aplicacoes_depositos",
                "especie",
                "outros",
            ),
        )
        fields = {
            "ano_exercicio": e,
            "nome": {"type": ["string", "null"], "maxLength": 200},
            "cpf_final": {"type": ["string", "null"], "maxLength": 3},
            "tem_recibo": {"type": "boolean"},
            "irpf_somente_recibo": {"type": "boolean"},
            "rendimentos_tributaveis": e,
            "rendimentos_isentos": e,
            "bens_direitos_total_atual": e,
            "bens_direitos_total_anterior": e,
            "bens_por_grupo": bens,
            "dividas_onus_atual": e,
            "dividas_onus_anterior": e,
            "participacoes": {"type": "array", "items": part},
        }
        data = _obj(fields, tuple(fields))
    elif familia == "CONTRATO_SOCIAL":
        socio = _obj(
            {
                "nome": {"type": ["string", "null"], "maxLength": 200},
                "administrador": {"type": "boolean"},
                "percentual_quotas": e,
            },
            ("nome", "administrador", "percentual_quotas"),
        )
        fields = {
            "data_ultima_alteracao": e,
            "numero_alteracao": {"type": ["string", "null"]},
            "capital_social": e,
            "capital_integralizado": e,
            "socios": {"type": "array", "items": socio},
            "objeto_resumo": {"type": ["string", "null"], "maxLength": 300},
            "uf_sede": {"type": ["string", "null"]},
        }
        data = _obj(fields, tuple(fields))
    elif familia == "ECF":
        data = _obj({}, ())
    else:
        data = _obj({}, ())
    return {
        "name": f"extrair_{familia.lower()}",
        "input_schema": _obj(
            {"familia": {"type": "string", "const": familia}, "dados": data},
            ("familia", "dados"),
        ),
    }


def _tool_result(response: Any) -> dict[str, Any]:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and isinstance(
            getattr(block, "input", None), dict
        ):
            return block.input
    raise RuntimeError("llm_sem_saida_estruturada")


def _usage(response: Any) -> tuple[int | None, int | None, float]:
    u = getattr(response, "usage", None)
    a, b = getattr(u, "input_tokens", None), getattr(u, "output_tokens", None)
    return a, b, round((a or 0) * 0.000003 + (b or 0) * 0.000015, 4)


def _call(client: anthropic.Anthropic, tool: dict[str, Any], prompt: str):
    return _call_with_limit(client, tool, prompt, max_tokens=3500)


def _call_with_limit(
    client: anthropic.Anthropic,
    tool: dict[str, Any],
    prompt: str,
    *,
    max_tokens: int,
) -> tuple[dict[str, Any], tuple[int | None, int | None, float], str | None]:
    """Calls Claude and retains its terminal reason for truncation handling."""
    r = client.messages.create(
        model=settings.CLAUDE_MODEL_EXTRACTION,
        max_tokens=max_tokens,
        temperature=0,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
    )
    stop_reason = getattr(r, "stop_reason", None)
    try:
        result = _tool_result(r)
    except RuntimeError:
        if stop_reason == "max_tokens":
            result = {}
        else:
            raise
    return result, _usage(r), stop_reason


def _safe_payload(value: Any) -> Any:
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            if str(k).lower() in FORBIDDEN_KEYS:
                continue
            out[str(k)] = (
                re.sub(r"\D", "", str(v or ""))[-3:] or None
                if k == "cpf_final"
                else _safe_payload(v)
            )
        return out
    if isinstance(value, list):
        return [_safe_payload(x) for x in value]
    return (
        re.sub(r"\b\d{3}[.\s-]?\d{3}[.\s-]?\d{3}-?\d{2}\b", "[CPF_REDACTED]", value)[
            :300
        ]
        if isinstance(value, str)
        else value
    )


def _prune(value: Any, schema: dict[str, Any]) -> Any:
    """Aplica o schema fechado tambem a objetos internos devolvidos pelo modelo."""
    if schema.get("type") == "object" and isinstance(value, dict):
        props = schema.get("properties", {})
        return {
            key: _prune(item, props[key]) for key, item in value.items() if key in props
        }
    if schema.get("type") == "array" and isinstance(value, list):
        return [_prune(item, schema.get("items", {})) for item in value]
    return value


def _whitelist(family: str, value: dict[str, Any]) -> dict[str, Any]:
    schema = _extraction_tool(family)["input_schema"]
    return _safe_payload(_prune({**value, "familia": family}, schema))


DEMONSTRACAO_FAMILIAS = {"SPED_ECD", "BALANCO_DRE", "BALANCETE", "BALANCO_ABERTURA"}


def _exercise_years(classificacao: dict[str, Any], text: str) -> list[int]:
    """Returns the years visible in a document, preserving classification first."""
    years: list[int] = []
    classified = classificacao.get("ano_referencia")
    if isinstance(classified, int) and 1900 <= classified <= 2100:
        years.append(classified)
    for value in re.findall(r"\b(19\d{2}|20\d{2})\b", text):
        year = int(value)
        if year not in years:
            years.append(year)
    return years[:12]


def _join_usage(
    *usages: tuple[int | None, int | None, float],
) -> tuple[int | None, int | None, float]:
    """Adds token usage without treating a missing provider value as zero in output."""
    return (
        sum(item[0] or 0 for item in usages),
        sum(item[1] or 0 for item in usages),
        round(sum(item[2] for item in usages), 4),
    )


def _extraction_prompt(family: str, text: str, year: int | None = None) -> str:
    scope = f" Extraia apenas o exercicio de {year}." if year else ""
    return (
        "Extraia somente o schema. Datas devem ser ISO (AAAA-MM-DD; competencia AAAA-MM)."
        " IRPF: grupo 06 codigo 10 ou descricao dinheiro em especie vai em especie, nunca em"
        " aplicacoes_depositos; grupo 06 codigo 01 (conta corrente) vai em aplicacoes_depositos,"
        " nunca em especie. IRPF nunca inclui endereco, banco, dependentes, pagamentos ou CPF completo."
        f"{scope}\n{text[:120000]}"
    )


def _extract_with_truncation_recovery(
    client: anthropic.Anthropic,
    family: str,
    classificacao: dict[str, Any],
    text: str,
    deadline: Deadline,
) -> tuple[dict[str, Any], tuple[int | None, int | None, float], bool]:
    """Extracts once at 8k tokens, then splits truncated demonstrations by exercise."""
    raw, usage, stop_reason = _call_with_limit(
        client,
        _extraction_tool(family),
        _extraction_prompt(family, text),
        max_tokens=8000,
    )
    if stop_reason != "max_tokens":
        return _whitelist(family, raw), usage, False
    if family not in DEMONSTRACAO_FAMILIAS:
        return _whitelist(family, raw), usage, True
    years = _exercise_years(classificacao, text)
    if not years:
        return _whitelist(family, raw), usage, True
    extracted: list[dict[str, Any]] = []
    usages = [usage]
    for year in years:
        deadline.remaining("reextracao_por_exercicio")
        retry_raw, retry_usage, retry_stop = _call_with_limit(
            client,
            _extraction_tool(family),
            _extraction_prompt(family, text, year),
            max_tokens=8000,
        )
        usages.append(retry_usage)
        if retry_stop == "max_tokens":
            return (
                {"familia": family, "dados": {"exercicios": extracted}},
                _join_usage(*usages),
                True,
            )
        extracted.extend(
            (_whitelist(family, retry_raw).get("dados") or {}).get("exercicios") or []
        )
    return (
        {"familia": family, "dados": {"exercicios": extracted}},
        _join_usage(*usages),
        False,
    )


def _empty_core_reason(
    family: str,
    dados: dict[str, Any],
    classificacao: dict[str, Any] | None = None,
) -> str | None:
    """Distinguishes valid zeroes from a model response with no usable core facts."""
    if family in DEMONSTRACAO_FAMILIAS and not dados.get("exercicios"):
        return "demonstracoes_vazias"
    if family == "FATURAMENTO" and not dados.get("meses"):
        return "faturamento_sem_meses"
    if family == "IRPF":
        groups = dados.get("bens_por_grupo") or {}
        fields = (
            dados.get("bens_direitos_total_atual"),
            dados.get("bens_direitos_total_anterior"),
            dados.get("rendimentos_tributaveis"),
            dados.get("rendimentos_isentos"),
            *groups.values(),
            *[item.get("valor") for item in dados.get("participacoes") or []],
        )
        if any(_num(field) is not None for field in fields):
            return None
        if dados.get("tem_recibo") or (classificacao or {}).get("tem_recibo_rfb"):
            dados["irpf_somente_recibo"] = True
            return None
        return "irpf_sem_bens_ou_rendimentos"
    return None


def _legacy_extraction_is_empty(row: dict[str, Any]) -> bool:
    """Makes old false-positive EXTRAIDO cache rows eligible for a safe retry."""
    payload = row.get("payload") or {}
    return (
        _empty_core_reason(
            str(row.get("familia") or payload.get("familia") or ""),
            payload.get("dados") or {},
            payload.get("classificacao") or {},
        )
        is not None
    )


def _confidence(c: dict[str, Any]) -> str:
    return (
        "ALTA"
        if c.get("tem_recibo_rfb")
        else "MEDIA"
        if c.get("assinado_contador")
        else "BAIXA"
    )


def _pages(pages: list[str], family: str, maximum: int) -> list[int]:
    if len(pages) <= maximum:
        return list(range(1, len(pages) + 1))
    candidates = [
        i + 1
        for i, text in enumerate(pages)
        if any(term in text.lower() for term in KEYWORDS.get(family, ()))
    ]
    return list(dict.fromkeys([*range(1, min(3, maximum) + 1), *candidates]))[:maximum]


def _existing(sha: str) -> dict[str, Any] | None:
    res = (
        supabase.table("documentos_extracoes")
        .select("*")
        .eq("sha256", sha)
        .eq("schema_versao", SCHEMA_VERSAO)
        .maybe_single()
        .execute()
    )
    return _single(res)


def _persist(row: dict[str, Any]) -> dict[str, Any]:
    rows = _rows(
        supabase.table("documentos_extracoes")
        .upsert(row, on_conflict="sha256")
        .execute()
    )
    return rows[0] if rows else row


def _extract_one(
    doc: dict[str, Any],
    client: anthropic.Anthropic,
    config: dict[str, Any],
    deadline: Deadline,
) -> dict[str, Any]:
    sha = str(doc["sha256"])
    old = _existing(sha)
    if old and old.get("status") == "IGNORADO_DERIVADO":
        return old
    if old and old.get("status") == "EXTRAIDO" and not _legacy_extraction_is_empty(old):
        return old
    attempts = int((old or {}).get("tentativas") or 0)
    if attempts >= MAX_ATTEMPTS:
        return old or {"sha256": sha, "status": "FALHA", "erro": "limite_tentativas"}
    try:
        deadline.remaining("proximo_documento")
        from app.services.storage_service import StorageService

        pdf, _, _ = normalize_document(
            StorageService().download_document_at_key(str(doc["storage_key"]))
        )
        pages = extract_pdf_pages(pdf, deadline)
        if len("".join(pages)) < 400:
            pages, _, _ = ocr_pdf(
                pdf, int(config.get("max_paginas_extracao", 40)), deadline
            )
        classificacao, custo_a, _ = _call(
            client,
            _classification_tool(),
            "Classifique pelo conteudo; nao repita dados pessoais.\n"
            + "".join(render_page(i + 1, t) for i, t in enumerate(pages[:3])),
        )
        family = str(classificacao.get("familia") or "OUTRO")
        if family in DERIVADAS:
            row = {
                "sha256": sha,
                "familia": family,
                "schema_versao": SCHEMA_VERSAO,
                "status": "IGNORADO_DERIVADO",
                "ano_referencia": classificacao.get("ano_referencia"),
                "payload": {
                    "familia": family,
                    "classificacao": _safe_payload(classificacao),
                },
                "modelo": settings.CLAUDE_MODEL_EXTRACTION,
                "tokens_entrada": custo_a[0],
                "tokens_saida": custo_a[1],
                "custo_usd": custo_a[2],
                "erro": None,
                "tentativas": attempts + 1,
            }
        else:
            text = "".join(
                render_page(i, pages[i - 1])
                for i in _pages(
                    pages, family, int(config.get("max_paginas_extracao", 40))
                )
            )
            good, custo_b, truncated = _extract_with_truncation_recovery(
                client,
                family,
                classificacao,
                text,
                deadline,
            )
            dados = good.get("dados") or {}
            empty_reason = _empty_core_reason(family, dados, classificacao)
            row = {
                "sha256": sha,
                "familia": family,
                "schema_versao": SCHEMA_VERSAO,
                "status": "PARCIAL" if truncated or empty_reason else "EXTRAIDO",
                "ano_referencia": classificacao.get("ano_referencia"),
                "payload": {
                    "familia": family,
                    "classificacao": _safe_payload(classificacao),
                    "dados": dados,
                    "confianca_fonte": _confidence(classificacao),
                },
                "modelo": settings.CLAUDE_MODEL_EXTRACTION,
                "tokens_entrada": (custo_a[0] or 0) + (custo_b[0] or 0),
                "tokens_saida": (custo_a[1] or 0) + (custo_b[1] or 0),
                "custo_usd": round(custo_a[2] + custo_b[2], 4),
                "erro": "saida_truncada" if truncated else empty_reason,
                "tentativas": attempts + 1,
            }
    except TimeoutError:
        return {
            "sha256": sha,
            "familia": (old or {}).get("familia", "OUTRO"),
            "status": "PARCIAL",
            "erro": "prazo_global",
            "tentativas": attempts,
        }
    except OCRUnavailableError:
        row = {
            "sha256": sha,
            "familia": "OUTRO",
            "schema_versao": SCHEMA_VERSAO,
            "status": "ILEGIVEL",
            "payload": {},
            "erro": "ocr_indisponivel",
            "tentativas": attempts + 1,
        }
    except Exception as exc:
        logger.warning(
            "documentos_financeiros.document_failed",
            sha256=sha,
            erro=exc.__class__.__name__,
        )
        row = {
            "sha256": sha,
            "familia": "OUTRO",
            "schema_versao": SCHEMA_VERSAO,
            "status": "FALHA",
            "payload": {},
            "erro": exc.__class__.__name__[:120],
            "tentativas": attempts + 1,
        }
    return _persist(row)


def _num(v: Any) -> float | None:
    """Converte evidencia numerica sem transformar ausencia em zero."""
    if isinstance(v, dict) and v.get("ausente_no_documento") is True:
        return None
    v = v.get("valor") if isinstance(v, dict) else v
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _rank(c: str | None) -> int:
    return {"ALTA": 3, "MEDIA": 2, "BAIXA": 1}.get(c or "", 0)


def _metric(n: float | None, d: float | None, motivo: str) -> dict[str, Any]:
    return {
        "valor": n / d if n is not None and d not in (None, 0) else None,
        "motivo": None if n is not None and d not in (None, 0) else motivo,
    }


def _minimum_evidence_confidence(*values: Any) -> str:
    """Returns the least confidence declared by the calculation inputs."""
    confidences = []
    for value in values:
        confidence = value.get("confianca") if isinstance(value, dict) else value
        if confidence in {"ALTA", "MEDIA", "BAIXA"}:
            confidences.append(confidence)
    return min(confidences, key=_rank) if confidences else "BAIXA"


def _as_date(value: Any) -> date | None:
    """Aceita datas ISO, brasileiras e mes/ano em portugues."""
    if isinstance(value, dict) and value.get("ausente_no_documento") is True:
        return None
    value = value.get("valor") if isinstance(value, dict) else value
    text = str(value or "").strip().lower()
    months = {
        "janeiro": 1,
        "fevereiro": 2,
        "marco": 3,
        "março": 3,
        "abril": 4,
        "maio": 5,
        "junho": 6,
        "julho": 7,
        "agosto": 8,
        "setembro": 9,
        "outubro": 10,
        "novembro": 11,
        "dezembro": 12,
    }
    for name, month in months.items():
        match = re.fullmatch(rf"{name}\s*/\s*(\d{{4}})", text)
        if match:
            return date(int(match.group(1)), month, 1)
    for pattern in (r"(\d{2})/(\d{2})/(\d{4})", r"(\d{4})-(\d{2})-(\d{2})"):
        match = re.fullmatch(pattern, text)
        if match:
            groups = [int(group) for group in match.groups()]
            return (
                date(groups[2], groups[1], groups[0]) if "/" in text else date(*groups)
            )
    match = re.fullmatch(r"(\d{2})/(\d{4})", text)
    if match:
        return date(int(match.group(2)), int(match.group(1)), 1)
    match = re.fullmatch(r"(\d{4})-(\d{2})", text)
    if match:
        return date(int(match.group(1)), int(match.group(2)), 1)
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


DATE_FIELDS = {
    "data_base",
    "data_emissao",
    "competencia",
    "data_ultima_alteracao",
    "vencimento_final",
}


def _normalize_dates(value: Any, field: str | None = None) -> Any:
    """Normaliza evidencias de data sem alterar sua proveniencia."""
    if isinstance(value, list):
        return [_normalize_dates(item, field) for item in value]
    if not isinstance(value, dict):
        return value
    original_data_base = (
        value.get("data_base", {}).get("valor")
        if isinstance(value.get("data_base"), dict)
        else None
    )
    normalized = {key: _normalize_dates(item, key) for key, item in value.items()}
    if field in DATE_FIELDS and "valor" in normalized:
        parsed = _as_date(normalized)
        if parsed:
            normalized["valor"] = (
                parsed.isoformat()[:7] if field == "competencia" else parsed.isoformat()
            )
    data_base = normalized.get("data_base")
    if (
        normalized.get("natureza") == "anual"
        and isinstance(data_base, dict)
        and re.fullmatch(r"\d{4}-\d{2}", str(original_data_base or ""))
    ):
        year, month = map(int, str(original_data_base).split("-"))
        data_base["valor"] = date(year, month, monthrange(year, month)[1]).isoformat()
    return normalized


def _strict_sum(values: list[float | None]) -> float | None:
    """Soma somente se todos os valores requeridos foram informados."""
    return None if any(value is None for value in values) else sum(values)


def _demonstration_source(demonstration: dict[str, Any], field: str) -> str | None:
    """Returns the source selected for one consolidated demonstration field."""
    source = demonstration.get("fonte")
    if isinstance(source, dict):
        return source.get(field) or demonstration.get("fonte_principal")
    return source or demonstration.get("fonte_principal")


def _consolidate(
    rows: list[dict[str, Any]], manifest: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Consolida somente fatos extraidos dos documentos do cliente."""
    config = config or {}
    exercises: dict[str, list[tuple[dict[str, Any], str, str, dict[str, Any]]]] = {}
    revenue: list[tuple[dict[str, Any], str, dict[str, Any]]] = []
    debts: list[dict[str, Any]] = []
    irpf: list[dict[str, Any]] = []
    socials: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "EXTRAIDO":
            continue
        p = row.get("payload") or {}
        d = _normalize_dates(p.get("dados") or {})
        f = row.get("familia")
        c = _confidence(p.get("classificacao") or {})
        source = {"sha256": row.get("sha256"), "familia": f, "confianca": c}
        if f in {"SPED_ECD", "BALANCO_DRE", "BALANCETE", "BALANCO_ABERTURA"}:
            for x in d.get("exercicios", []):
                data_base = _as_date(x.get("data_base"))
                key = data_base.strftime("%Y-%m") if data_base else None
                exercises.setdefault(str(key), []).append((x, f, c, source))
        elif f == "FATURAMENTO":
            revenue.append((d, c, source))
        elif f in {"SCR", "ENDIVIDAMENTO_DECLARADO"}:
            debts.append({"familia": f, "dados": d, **source})
        elif f == "IRPF":
            irpf.append({"dados": d, **source})
        elif f == "CONTRATO_SOCIAL":
            socials.append({"dados": d, **source})
    demos = []
    diverg = []
    preference = {
        "SPED_ECD": 4,
        "BALANCO_DRE": 3,
        "BALANCETE": 2,
        "BALANCO_ABERTURA": 1,
    }
    for year, items in exercises.items():
        items.sort(key=lambda x: (_rank(x[2]), preference[x[1]]), reverse=True)
        patrimonial = next(
            (
                item
                for item in items
                if _num(item[0].get("ativo_total")) is not None
                and _num(item[0].get("patrimonio_liquido")) is not None
            ),
            items[0],
        )
        x, f, c, source = patrimonial
        merged: dict[str, Any] = {}
        field_sources: dict[str, str] = {}
        for field in PATRIMONIAL_FIELDS:
            evidence = x.get(field)
            if isinstance(evidence, dict):
                evidence = {
                    **evidence,
                    "fonte": f,
                    "sha256": source.get("sha256"),
                }
            merged[field] = evidence
            if _num(evidence) is not None:
                field_sources[field] = f
        for field in RESULT_FIELDS:
            available = [item for item in items if _num(item[0].get(field)) is not None]
            if not available:
                merged[field] = x.get(field)
                continue
            available.sort(
                key=lambda item: (
                    _rank(_minimum_evidence_confidence(item[2], item[0].get(field))),
                    preference[item[1]],
                ),
                reverse=True,
            )
            chosen, chosen_family, _, chosen_source = available[0]
            evidence = chosen.get(field)
            if isinstance(evidence, dict):
                evidence = {
                    **evidence,
                    "fonte": chosen_family,
                    "sha256": chosen_source.get("sha256"),
                }
            merged[field] = evidence
            field_sources[field] = chosen_family
        demos.append(
            {
                "data_base": x.get("data_base"),
                "natureza": x.get("natureza"),
                "fonte": field_sources,
                "fonte_principal": f,
                "confianca": c,
                "sha256": source.get("sha256"),
                "familia": source.get("familia"),
                **merged,
            }
        )
        for k in BALANCE:
            vals = [
                (
                    family,
                    _num(item.get(k)),
                    _minimum_evidence_confidence(confidence, item.get(k)),
                    meta,
                )
                for item, family, confidence, meta in items
                if _num(item.get(k)) is not None
            ]
            if (
                len(vals) > 1
                and min(value for _, value, _, _ in vals)
                and (
                    max(value for _, value, _, _ in vals)
                    - min(value for _, value, _, _ in vals)
                )
                / min(value for _, value, _, _ in vals)
                > 0.05
            ):
                diverg.append(
                    {
                        "exercicio": year,
                        "campo": k,
                        "fontes": [family for family, _, _, _ in vals],
                        "sha256": [meta.get("sha256") for _, _, _, meta in vals],
                        "confianca": _minimum_evidence_confidence(
                            *(confidence for _, _, confidence, _ in vals)
                        ),
                    }
                )
    demos.sort(
        key=lambda item: (
            _as_date(item.get("data_base")) or date.min,
            {"anual": 3, "intermediario": 2, "abertura": 1}.get(
                item.get("natureza"), 0
            ),
        ),
        reverse=True,
    )
    revenue.sort(
        key=lambda item: (
            _as_date(item[0].get("data_emissao"))
            or max(
                (
                    _as_date(month.get("competencia")) or date.min
                    for month in item[0].get("meses", [])
                ),
                default=date.min,
            )
        ),
        reverse=True,
    )
    socials.sort(
        key=lambda item: _as_date(item["dados"].get("data_ultima_alteracao"))
        or date.min,
        reverse=True,
    )
    fat, faturamento_confianca, faturamento_source = (
        revenue[0] if revenue else ({}, "BAIXA", {})
    )
    meses = fat.get("meses") or []
    soma = _strict_sum([_num(item.get("valor")) for item in meses]) if meses else None
    declarado = _num(fat.get("total_declarado"))
    cessoes = [
        item
        for debt in debts
        for item in debt["dados"].get("credores", [])
        if item.get("cessao_recebiveis")
    ]
    aval = [
        {
            "nome": x.get("nome"),
            "cpf_final": x.get("cpf_final"),
            "irpf_somente_recibo": bool(x.get("irpf_somente_recibo")),
            "confianca": _minimum_evidence_confidence(
                source.get("confianca"),
                x.get("bens_direitos_total_atual"),
                x.get("dividas_onus_atual"),
            ),
            "sha256": source.get("sha256"),
            "familia": source.get("familia"),
            "ano": _num(x.get("ano_exercicio")),
            "renda": _strict_sum(
                [
                    _num(x.get("rendimentos_tributaveis")),
                    _num(x.get("rendimentos_isentos")),
                ]
            ),
            "bens": _num(x.get("bens_direitos_total_atual")),
            "dividas": _num(x.get("dividas_onus_atual")),
            "patrimonio_liquido_declarado": (
                _num(x.get("bens_direitos_total_atual"))
                - _num(x.get("dividas_onus_atual"))
                if _num(x.get("bens_direitos_total_atual")) is not None
                and _num(x.get("dividas_onus_atual")) is not None
                else None
            ),
            "bens_por_grupo": {
                key: _num(value)
                for key, value in (x.get("bens_por_grupo") or {}).items()
            },
            "participacoes": [
                {
                    **item,
                    "cnpj": re.sub(r"\D", "", str(item.get("cnpj") or "")) or None,
                    "valor": _num(item.get("valor")),
                }
                for item in (x.get("participacoes") or [])
            ],
        }
        for source in irpf
        for x in [source["dados"]]
    ]
    cur = next(
        (item for item in demos if item.get("natureza") == "anual"),
        demos[0] if demos else {},
    )
    societario = (
        {**socials[0]["dados"], "confianca": socials[0]["confianca"]}
        if socials
        else None
    )
    capital_balanco = _num(cur.get("capital_social"))
    capital_societario = _num((societario or {}).get("capital_social"))
    if (
        capital_balanco not in (None, 0)
        and capital_societario is not None
        and abs(capital_balanco - capital_societario) / abs(capital_balanco) > 0.05
    ):
        diverg.append(
            {
                "campo": "capital_social",
                "fonte_a": "balanco",
                "valor_a": capital_balanco,
                "fonte_b": "contrato_social",
                "valor_b": capital_societario,
            }
        )
    for avalista in aval:
        for participacao in avalista["participacoes"]:
            participacao_valor = _num(participacao.get("valor"))
            if (
                capital_societario not in (None, 0)
                and participacao_valor is not None
                and abs(participacao_valor - capital_societario)
                / abs(capital_societario)
                > 0.05
            ):
                diverg.append(
                    {
                        "campo": "participacao_socio_capital_social",
                        "fonte_a": "irpf",
                        "valor_a": participacao_valor,
                        "fonte_b": "contrato_social",
                        "valor_b": capital_societario,
                    }
                )
    ativo, pc, pnc, pl = map(
        _num,
        [
            cur.get("ativo_total"),
            cur.get("passivo_circulante"),
            cur.get("passivo_nao_circulante"),
            cur.get("patrimonio_liquido"),
        ],
    )
    creditors = [item for debt in debts for item in debt["dados"].get("credores", [])]
    debt_values = [_num(item.get("parcela_mensal")) for item in creditors]
    missing_installments = any(
        (_num(item.get("saldo_devedor")) or 0) > 0
        and _num(item.get("parcela_mensal")) is None
        for item in creditors
    )
    parcela = None if missing_installments else _strict_sum(debt_values)

    def check(code, v, detail, confidence="BAIXA"):
        return {
            "codigo": code,
            "resultado": "OK"
            if v is True
            else "FALHA"
            if v is False
            else "NAO_VERIFICADO",
            "detalhe": detail,
            "confianca": confidence,
        }

    base = _as_date(cur.get("data_base"))
    emissao = _as_date(fat.get("data_emissao"))
    today = date.today()
    balanco_atual = (
        None
        if base is None
        else (today.year - base.year) * 12 + today.month - base.month
        <= int((config or {}).get("balanco_validade_meses", 18))
    )
    faturamento_atual = (
        None
        if emissao is None
        else (today.year - emissao.year) * 12 + today.month - emissao.month
        <= int((config or {}).get("faturamento_defasagem_meses", 3))
    )
    checks = [
        check(
            "balanco_fecha",
            None
            if None in (ativo, pc, pl)
            else abs(ativo - (pc + (pnc or 0) + pl)) <= max(ativo, 1) * 0.01,
            "PNC inexistente"
            if pnc is None and None not in (ativo, pc, pl)
            else "ativo = passivo + PL",
            _minimum_evidence_confidence(
                cur.get("confianca"),
                cur.get("ativo_total"),
                cur.get("passivo_circulante"),
                cur.get("passivo_nao_circulante"),
                cur.get("patrimonio_liquido"),
            ),
        ),
        check(
            "soma_faturamento",
            None
            if declarado is None or soma is None
            else abs(soma - declarado) <= max(declarado, 1) * 0.01,
            "soma mensal versus declarado",
            _minimum_evidence_confidence(
                faturamento_confianca,
                fat.get("total_declarado"),
                *[month.get("valor") for month in meses],
            ),
        ),
        check(
            "balanco_atual",
            balanco_atual,
            "balanco ate 18 meses",
            _minimum_evidence_confidence(cur.get("confianca"), cur.get("data_base")),
        ),
        check(
            "faturamento_atual",
            faturamento_atual,
            "faturamento ate 3 meses",
            _minimum_evidence_confidence(
                faturamento_confianca, fat.get("data_emissao")
            ),
        ),
    ]
    scr_source = next((item for item in debts if item["familia"] == "SCR"), None)
    declared_source = next(
        (item for item in debts if item["familia"] == "ENDIVIDAMENTO_DECLARADO"),
        None,
    )
    scr = (scr_source or {}).get("dados")
    declared = (declared_source or {}).get("dados")
    if scr:
        declared_total = _strict_sum(
            [
                _num(scr.get("carteira_a_vencer")),
                _num(scr.get("vencido")),
                _num(scr.get("prejuizo")),
            ]
        )
        declared_base = scr.get("data_base")
    else:
        declared_total = _num((declared or {}).get("total_declarado"))
        declared_base = (declared or {}).get("data_base")
    balance_debt = (
        _strict_sum([_num(cur.get("emprestimos_cp")), _num(cur.get("emprestimos_lp"))])
        if cur
        else None
    )
    inds = {
        "liquidez_corrente": _metric(
            _num(cur.get("ativo_circulante")),
            pc,
            "ativo_circulante_ou_passivo_circulante_ausente",
        ),
        "alavancagem": _metric(
            balance_debt,
            pl,
            "emprestimos_ou_PL_ausente",
        ),
        "alavancagem_declarada": _metric(
            declared_total, pl, "endividamento_declarado_ou_PL_ausente"
        ),
        "margem_liquida": _metric(
            _num(cur.get("resultado_liquido")),
            _num(cur.get("receita_liquida")),
            "resultado_ou_receita_ausente",
        ),
        "servico_divida_sobre_faturamento": _metric(
            parcela,
            soma / 12 if soma else None,
            "parcelas_nao_informadas"
            if missing_installments
            else "parcelas_ou_faturamento_ausente",
        ),
    }
    debt_confidence = _minimum_evidence_confidence(
        *(item.get("confianca") for item in debts)
    )
    inds["liquidez_corrente"]["confianca"] = _minimum_evidence_confidence(
        cur.get("confianca"),
        cur.get("ativo_circulante"),
        cur.get("passivo_circulante"),
    )
    inds["alavancagem"]["confianca"] = _minimum_evidence_confidence(
        cur.get("confianca"),
        cur.get("emprestimos_cp"),
        cur.get("emprestimos_lp"),
        cur.get("patrimonio_liquido"),
    )
    inds["alavancagem_declarada"]["confianca"] = _minimum_evidence_confidence(
        debt_confidence,
        cur.get("confianca"),
        cur.get("patrimonio_liquido"),
    )
    inds["margem_liquida"]["confianca"] = _minimum_evidence_confidence(
        cur.get("confianca"),
        cur.get("resultado_liquido"),
        cur.get("receita_liquida"),
    )
    inds["servico_divida_sobre_faturamento"]["confianca"] = (
        _minimum_evidence_confidence(debt_confidence, faturamento_confianca)
    )
    inds["liquidez_corrente"]["fonte"] = _demonstration_source(cur, "ativo_circulante")
    inds["alavancagem"]["fonte"] = _demonstration_source(cur, "emprestimos_cp")
    inds["alavancagem_declarada"]["fonte"] = (scr_source or declared_source or {}).get(
        "familia"
    )
    inds["margem_liquida"]["fonte"] = _demonstration_source(cur, "resultado_liquido")
    inds["servico_divida_sobre_faturamento"]["fonte"] = (
        scr_source or declared_source or {}
    ).get("familia") or faturamento_source.get("familia")
    return {
        "schema_versao": SCHEMA_VERSAO,
        "manifest_documentos": manifest,
        "documentos": [
            {
                "sha256": r.get("sha256"),
                "familia": r.get("familia"),
                "status": r.get("status"),
                "ano_referencia": r.get("ano_referencia"),
                "confianca_fonte": (r.get("payload") or {}).get("confianca_fonte"),
            }
            for r in rows
        ],
        "demonstracoes": demos,
        "divergencias": diverg,
        "faturamento_12m": {
            "meses": meses,
            "soma_calculada": soma,
            "total_declarado": fat.get("total_declarado"),
            "confianca_fonte": faturamento_confianca,
            "fonte": faturamento_source.get("familia"),
            "sha256": faturamento_source.get("sha256"),
            "confianca": _minimum_evidence_confidence(
                faturamento_confianca,
                fat.get("total_declarado"),
                *[month.get("valor") for month in meses],
            ),
        }
        if fat
        else None,
        "endividamento": {
            "fontes": debts,
            "cessoes_recebiveis": cessoes,
            "total_declarado": declared_total,
            "data_base": declared_base,
        },
        "avalistas": aval,
        "societario": societario,
        "verificacoes": checks,
        "indicadores": inds,
    }


def _manifest(docs: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        "|".join(sorted(str(x["sha256"]) for x in docs if x.get("sha256"))).encode()
    ).hexdigest()


def _config() -> dict[str, Any]:
    rows = _rows(
        supabase.table("documentos_financeiros_config").select("*").limit(1).execute()
    )
    return rows[0] if rows else {}


def extract_documentos_financeiros(
    cnpj: str, *, operation_id: str | None = None
) -> dict[str, Any]:
    docs = _rows(
        supabase.table("documentos_cliente")
        .select("sha256,storage_key,dono_tipo")
        .eq("cnpj", cnpj)
        .eq("status", "CAPTURADO")
        .not_.is_("sha256", "null")
        .execute()
    )
    unique = {str(x["sha256"]): x for x in docs if x.get("storage_key")}
    manifest = _manifest(list(unique.values()))
    config = _config()
    if not unique:
        return _consolidate([], manifest, config)
    deadline = Deadline.start(GLOBAL_TIMEOUT_SECONDS)
    used = 0.0
    limit = float(config.get("max_custo_usd_operacao", 1))
    result = []
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=180)
    try:
        for doc in unique.values():
            try:
                deadline.remaining("fila_documentos")
            except TimeoutError:
                result.append(
                    {
                        "sha256": doc["sha256"],
                        "familia": "OUTRO",
                        "status": "PARCIAL",
                        "erro": "prazo_global",
                    }
                )
                continue
            if used >= limit:
                result.append(
                    {
                        "sha256": doc["sha256"],
                        "familia": "OUTRO",
                        "status": "FALHA",
                        "erro": "limite_custo",
                    }
                )
                continue
            row = _extract_one(doc, client, config, deadline)
            used += float(row.get("custo_usd") or 0)
            result.append(row)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    consolidated = _consolidate(result, manifest, config)
    # BaseComponentTask nao guarda no cache resultados ainda retentaveis.
    if any(row.get("status") in {"FALHA", "ILEGIVEL", "PARCIAL"} for row in result):
        consolidated["erro"] = "documentos_pendentes"
    return consolidated
