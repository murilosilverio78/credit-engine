"""Extracao financeira preserva dados minimizados e nao emite achados."""

from __future__ import annotations
import hashlib
import re
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
SCHEMA_VERSAO, MAX_ATTEMPTS, GLOBAL_TIMEOUT_SECONDS = 1, 3, 840
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
                    "outros",
                )
            },
            (
                "imoveis",
                "veiculos",
                "participacoes_societarias",
                "aplicacoes_depositos",
                "outros",
            ),
        )
        fields = {
            "ano_exercicio": e,
            "nome": {"type": ["string", "null"], "maxLength": 200},
            "cpf_final": {"type": ["string", "null"], "maxLength": 3},
            "tem_recibo": {"type": "boolean"},
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
    r = client.messages.create(
        model=settings.CLAUDE_MODEL_EXTRACTION,
        max_tokens=3500,
        temperature=0,
        tools=[tool],
        tool_choice={"type": "tool", "name": tool["name"]},
        messages=[{"role": "user", "content": prompt}],
    )
    return _tool_result(r), _usage(r)


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


def _confidence(c: dict[str, Any]) -> str:
    return (
        "ALTA"
        if c.get("familia") == "SPED_ECD" and c.get("tem_recibo_rfb")
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
    if old and old.get("status") in {"EXTRAIDO", "PARCIAL", "IGNORADO_DERIVADO"}:
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
        classificacao, custo_a = _call(
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
            raw, custo_b = _call(
                client,
                _extraction_tool(family),
                "Extraia somente o schema. IRPF nunca inclui endereco, banco, dependentes, pagamentos ou CPF completo.\n"
                + text[:120000],
            )
            good = _whitelist(family, raw)
            row = {
                "sha256": sha,
                "familia": family,
                "schema_versao": SCHEMA_VERSAO,
                "status": "EXTRAIDO",
                "ano_referencia": classificacao.get("ano_referencia"),
                "payload": {
                    "familia": family,
                    "classificacao": _safe_payload(classificacao),
                    "dados": good.get("dados", {}),
                    "confianca_fonte": _confidence(classificacao),
                },
                "modelo": settings.CLAUDE_MODEL_EXTRACTION,
                "tokens_entrada": (custo_a[0] or 0) + (custo_b[0] or 0),
                "tokens_saida": (custo_a[1] or 0) + (custo_b[1] or 0),
                "custo_usd": round(custo_a[2] + custo_b[2], 4),
                "erro": None,
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


def _as_date(value: Any) -> date | None:
    value = value.get("valor") if isinstance(value, dict) else value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _consolidate(
    rows: list[dict[str, Any]], manifest: str, config: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Consolida somente fatos extraidos dos documentos do cliente."""
    config = config or {}
    exercises: dict[str, list[tuple[dict[str, Any], str, str]]] = {}
    revenue: list[tuple[dict[str, Any], str]] = []
    debts: list[dict[str, Any]] = []
    irpf: list[dict[str, Any]] = []
    socials: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "EXTRAIDO":
            continue
        p = row.get("payload") or {}
        d = p.get("dados") or {}
        f = row.get("familia")
        c = p.get("confianca_fonte", "BAIXA")
        if f in {"SPED_ECD", "BALANCO_DRE", "BALANCETE", "BALANCO_ABERTURA"}:
            for x in d.get("exercicios", []):
                exercises.setdefault(
                    str((x.get("data_base") or {}).get("valor")), []
                ).append((x, f, c))
        elif f == "FATURAMENTO":
            revenue.append((d, c))
        elif f in {"SCR", "ENDIVIDAMENTO_DECLARADO"}:
            debts.append({"familia": f, "dados": d})
        elif f == "IRPF":
            irpf.append(d)
        elif f == "CONTRATO_SOCIAL":
            socials.append(d)
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
        x, f, c = items[0]
        demos.append(
            {
                "data_base": x.get("data_base"),
                "natureza": x.get("natureza"),
                "fonte": f,
                "confianca": c,
                **{k: x.get(k) for k in BALANCE},
            }
        )
        for k in BALANCE:
            vals = [
                (source, _num(item.get(k)))
                for item, source, _ in items
                if _num(item.get(k)) is not None
            ]
            if (
                len(vals) > 1
                and min(v for _, v in vals)
                and (max(v for _, v in vals) - min(v for _, v in vals))
                / min(v for _, v in vals)
                > 0.05
            ):
                diverg.append(
                    {"exercicio": year, "campo": k, "fontes": [s for s, _ in vals]}
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
        key=lambda item: _as_date(item.get("data_ultima_alteracao")) or date.min,
        reverse=True,
    )
    fat = revenue[0][0] if revenue else {}
    meses = fat.get("meses") or []
    soma = sum(_num(item.get("valor")) or 0 for item in meses)
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
            "ano": _num(x.get("ano_exercicio")),
            "renda": (_num(x.get("rendimentos_tributaveis")) or 0)
            + (_num(x.get("rendimentos_isentos")) or 0),
            "bens": _num(x.get("bens_direitos_total_atual")),
            "dividas": _num(x.get("dividas_onus_atual")),
            "patrimonio_liquido_declarado": (
                _num(x.get("bens_direitos_total_atual")) or 0
            )
            - (_num(x.get("dividas_onus_atual")) or 0),
            "participacoes": x.get("participacoes") or [],
        }
        for x in irpf
    ]
    cur = next(
        (item for item in demos if item.get("natureza") == "anual"),
        demos[0] if demos else {},
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
    parcela = sum(
        _num(item.get("parcela_mensal")) or 0
        for debt in debts
        for item in debt["dados"].get("credores", [])
    )

    def check(code, v, detail):
        return {
            "codigo": code,
            "resultado": "OK"
            if v is True
            else "FALHA"
            if v is False
            else "NAO_VERIFICADO",
            "detalhe": detail,
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
            if None in (ativo, pc, pnc, pl)
            else abs(ativo - (pc + pnc + pl)) <= max(ativo, 1) * 0.01,
            "ativo = passivo + PL",
        ),
        check(
            "soma_faturamento",
            None
            if declarado is None
            else abs(soma - declarado) <= max(declarado, 1) * 0.01,
            "soma mensal versus declarado",
        ),
        check("balanco_atual", balanco_atual, "balanco ate 18 meses"),
        check("faturamento_atual", faturamento_atual, "faturamento ate 3 meses"),
    ]
    inds = {
        "liquidez_corrente": _metric(
            _num(cur.get("ativo_circulante")),
            pc,
            "ativo_circulante_ou_passivo_circulante_ausente",
        ),
        "alavancagem": _metric(
            (_num(cur.get("emprestimos_cp")) or 0)
            + (_num(cur.get("emprestimos_lp")) or 0)
            if cur
            else None,
            pl,
            "emprestimos_ou_PL_ausente",
        ),
        "margem_liquida": _metric(
            _num(cur.get("resultado_liquido")),
            _num(cur.get("receita_liquida")),
            "resultado_ou_receita_ausente",
        ),
        "servico_divida_sobre_faturamento": _metric(
            parcela, soma / 12 if soma else None, "parcelas_ou_faturamento_ausente"
        ),
    }
    return {
        "schema_versao": SCHEMA_VERSAO,
        "manifest_documentos": manifest,
        "documentos": [
            {
                "sha256": r.get("sha256"),
                "familia": r.get("familia"),
                "status": r.get("status"),
                "ano_referencia": r.get("ano_referencia"),
            }
            for r in rows
        ],
        "demonstracoes": demos,
        "divergencias": diverg,
        "faturamento_12m": {
            "meses": meses,
            "soma_calculada": soma,
            "total_declarado": fat.get("total_declarado"),
        }
        if fat
        else None,
        "endividamento": {"fontes": debts, "cessoes_recebiveis": cessoes},
        "avalistas": aval,
        "societario": socials[0] if socials else None,
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
