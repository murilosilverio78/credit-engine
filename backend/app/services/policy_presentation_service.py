"""Read-only projection of the latest shadow-policy decision for reports."""

from __future__ import annotations

from typing import Any


_MISSING_TEXT = {
    "porte": "porte e operacionalidade (sai no relatório completo)",
    "sacado_orgao": "sacado e órgão",
    "cadastro_regularidade": "cadastro e regularidade",
}
_FIELD_LABELS = {
    "capital_social": "Capital social",
    "participacao_socio_capital_social": "Participação do sócio no capital",
    "participacao_socio_capital": "Participação do sócio no capital",
    "margem_liquida": "Margem líquida",
    "receita": "Receita",
}
_SOURCE_LABELS = {
    "irpf": "IRPF do sócio",
    "contrato_social": "contrato social",
    "balanco": "balanço",
    "dre": "DRE",
    "sped_ecd": "SPED ECD",
}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _money(value: Any) -> str:
    amount = _number(value)
    if amount is None:
        return "não informado"
    if abs(amount) >= 1_000_000:
        return f"R$ {amount / 1_000_000:,.2f} mi".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {amount:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _percent(value: Any) -> str:
    amount = _number(value)
    if amount is None:
        return "não informado"
    percentage = amount * 100
    digits = 0 if abs(percentage) > 10 else 1
    return f"{percentage:.{digits}f}%".replace(".", ",")


def _quantity(value: Any) -> str:
    amount = _number(value)
    return f"{amount:g}".replace(".", ",") if amount is not None else "não informado"


def _label(value: Any) -> str:
    raw = str(value or "")
    return _FIELD_LABELS.get(raw, raw.replace("_", " ").capitalize())


def _generic_value(value: Any, key: str = "") -> str:
    if isinstance(value, dict):
        return "; ".join(
            f"{_label(name)}: {_generic_value(item, str(name))}"
            for name, item in value.items()
        ) or "não informado"
    if isinstance(value, list):
        return "; ".join(_generic_value(item, key) for item in value) or "não informado"
    if any(term in key.lower() for term in ("pct", "margem", "cobertura", "participacao")):
        return _percent(value)
    if any(term in key.lower() for term in ("valor", "receita", "capital", "patrimonio", "pl_")):
        return _money(value)
    if value is None or value == "":
        return "não informado"
    return str(value)


def _divergence_value(value: Any, field: Any) -> str:
    """Formats documented divergence values by their field semantics."""
    name = str(field or "").lower()
    if name.endswith("_pct") or "percentual" in name or "%" in name:
        return _percent(value)
    if any(term in name for term in ("capital", "participacao", "receita", "patrimonio")):
        return _money(value)
    return _generic_value(value, name)


def _informative_text(code: str, value: Any, label: str) -> list[str]:
    data = _record(value)
    if code == "aval_cobertura":
        return [
            "Aval cobre "
            f"{_percent(data.get('cobertura'))} da operação "
            f"(patrimônio ajustado {_money(data.get('pl_ajustado'))} de "
            f"{_money(data.get('pl_declarado'))} declarados)"
        ]
    if code == "aval_patrimonio_nao_verificavel":
        people = value if isinstance(value, list) else []
        texts = []
        for item in map(_record, people):
            person = f"{item.get('nome') or 'Avalista'} (CPF final {item.get('cpf_final') or 'não informado'})"
            if _number(item.get("pct_total")) == 0:
                reason = "sem patrimônio comprovado"
            elif (_number(item.get("pct_quotas_cedente")) or 0) >= 0.5:
                reason = f"{_percent(item.get('pct_quotas_cedente'))} do patrimônio são quotas da própria empresa"
            else:
                reason = "patrimônio majoritariamente não líquido"
            texts.append(f"{person}: {reason}")
        return texts
    if code == "dependencia_publica":
        return [f"{_percent(value)} da receita vem de pagamentos públicos"]
    if code == "divergencia_documental":
        items = value if isinstance(value, list) else [data]
        return [
            f"{_label(item.get('campo'))}: {_SOURCE_LABELS.get(str(item.get('fonte_a')), str(item.get('fonte_a') or 'Fonte A'))} diz {_divergence_value(item.get('valor_a'), item.get('campo'))}, "
            f"{_SOURCE_LABELS.get(str(item.get('fonte_b')), str(item.get('fonte_b') or 'Fonte B'))} diz {_divergence_value(item.get('valor_b'), item.get('campo'))}"
            for item in map(_record, items)
        ]
    if code == "margem_liquida":
        return [f"Margem líquida de {_percent(value)}"]
    if code == "receita_declarada_12m":
        return [f"Receita declarada nos últimos 12 meses: {_money(value)}"]
    if code == "mensalidade_faturada_media":
        return [f"Mensalidade faturada média: {_money(data.get('valor'))} (CV {_percent(data.get('cv'))})"]
    if code == "meses_sem_fatura_12m":
        return [f"{int(_number(value) or 0)} meses sem fatura no histórico de 12 meses"]
    if code == "glosa_pct_12m":
        return [f"Glosa de {_percent(value)} das faturas"]
    if code == "prazo_ateste_dias":
        return [f"Prazo mediano de ateste: {int(_number(value) or 0)} dias"]
    if code == "prazo_liquidacao_dias":
        return [f"Prazo mediano de liquidação: {int(_number(value) or 0)} dias"]
    if code == "saldo_empenho_ano":
        return [f"Saldo de empenho no exercício: {_money(data.get('saldo'))} ({_quantity(data.get('meses_cobertos'))} meses cobertos)"]
    if code == "empenho_insuficiente":
        return [f"Empenho insuficiente: {_money(data.get('valor_descoberto'))} descobertos em {_quantity(data.get('meses_descobertos'))} meses"]
    if code == "cobertura_parcela":
        coverage = data.get("indice") if isinstance(data, dict) else value
        return [f"Cobertura da parcela estimada: {_percent(coverage)}"]
    if code == "execucao_vs_contratado":
        return [f"Execução faturada versus contratado: {_percent(value)}"]
    if code == "prazo_pagamento_dias":
        return [f"Órgão paga em média {int(_number(value) or 0)} dias após a liquidação"]
    if code == "ciclo_caixa_dias":
        return [f"Ciclo total de {int(_number(value) or 0)} dias"]
    if code == "faturas_liquidadas_sem_pagamento":
        return [f"{int(_number(data.get('quantidade')) or 0)} faturas liquidadas há mais de 30 dias sem pagamento ({_money(data.get('valor'))})"]
    if code == "taxa_conciliacao_pagamentos":
        return [f"Taxa de conciliação de pagamentos: {_percent(value)}"]
    if code == "pago_portal_vs_comprasnet":
        return [f"Pagamentos no Portal: {_money(data.get('pago_portal_ano'))} versus {_money(data.get('liquidado_comprasnet_ano'))} liquidados no Comprasnet"]
    if code == "antecipacao_concorrente":
        return [
            f"{int(_number(data.get('quantidade')) or 0)} antecipações ou cessões concorrentes identificadas "
            f"({_money(data.get('valor'))}); operações {', '.join(map(str, data.get('numeros_operacao') or [])) or 'não informadas'}"
        ]
    if code == "pagamentos_contrato_12m":
        return [
            f"Pagamentos do contrato nos últimos 12 meses: {_money(data.get('valor'))} em "
            f"{int(_number(data.get('quantidade')) or 0)} ordens"
        ]
    return [f"{label}: {_generic_value(value, code)}"]


def _rows(query) -> list[dict[str, Any]]:
    result = query.execute()
    return list(getattr(result, "data", None) or [])


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _codes(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [code for item in value.values() for code in _codes(item)]
    if isinstance(value, list):
        return [code for item in value for code in _codes(item)]
    return []


def _evidence(value: Any, documents: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    public: list[dict[str, Any]] = []
    for raw in items:
        item = dict(raw) if isinstance(raw, dict) else {"valor": raw}
        sha256 = item.get("sha256")
        document = documents.get(str(sha256)) if sha256 else None
        if document:
            item.pop("storage_key", None)
            item["documento"] = {
                "documento_id": document.get("id"),
                "nome_arquivo": document.get("nome_arquivo"),
                "familia": document.get("tipo"),
                "data_base": item.get("data_base") or item.get("data") or document.get("capturado_em"),
                "pagina": item.get("pagina"),
                "trecho": item.get("trecho") or item.get("texto"),
            }
        public.append(item)
    return public


def _finding_projection(finding: dict[str, Any] | None, labels: dict[str, str], documents: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    if not finding:
        return None
    code = str(finding.get("codigo") or "")
    return {
        "codigo": code,
        "rotulo": labels.get(code) or code.replace("_", " ").capitalize(),
        "estado": finding.get("estado"),
        "confianca": finding.get("confianca"),
        "valor": finding.get("valor"),
        "evidencia": _evidence(finding.get("evidencia"), documents),
    }


def latest_policy_view(operation_id: str, *, database=None, operation: dict[str, Any] | None = None) -> dict[str, Any]:
    """Return a report-safe view; it never changes the official decision."""
    if database is None:
        from app.core.database import supabase
        database = supabase
    operation = operation or (_rows(database.table("operations").select("id,cnpj,rating,limite_aprovado").eq("id", operation_id).limit(1)) or [None])[0]
    if not operation:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Operação não encontrada")

    runs = _rows(
        database.table("policy_shadow_runs").select("*").eq("operation_id", operation_id).order("created_at", desc=True)
    )
    if not runs:
        return {"status": "SEM_AVALIACAO"}
    versions = _rows(database.table("policy_versions").select("id,versao,status").in_("id", [row.get("policy_version_id") for row in runs]))
    version_by_id = {str(row.get("id")): row for row in versions}
    # A SOMBRA is authoritative for this informational panel; ATIVA is a fallback.
    candidates = [row for row in runs if version_by_id.get(str(row.get("policy_version_id")), {}).get("status") in {"SOMBRA", "ATIVA"}]
    shadows = [row for row in candidates if version_by_id.get(str(row.get("policy_version_id")), {}).get("status") == "SOMBRA"]
    candidates = shadows or candidates
    candidates.sort(key=lambda row: str(row.get("created_at") or ""), reverse=True)
    if not candidates:
        return {"status": "SEM_AVALIACAO"}
    shadow = candidates[0]
    version = _record(version_by_id.get(str(shadow.get("policy_version_id"))))
    result = _record(shadow.get("resultado"))
    used_runs = set(_codes(shadow.get("runs_usados")))
    findings = _rows(database.table("findings").select("codigo,estado,confianca,valor,evidencia,run_id").eq("operation_id", operation_id))
    # Cash-flow findings are informational even while policy v2 stays frozen.
    # They may have been emitted after the selected shadow run, so include the
    # newest fluxo_caixa run explicitly rather than changing policy inputs.
    try:
        cash_runs = _rows(database.table("finding_runs").select("id").eq("operation_id", operation_id).eq("especialista", "fluxo_caixa").order("created_at", desc=True).limit(1))
        used_runs.update(str(row.get("id")) for row in cash_runs if row.get("id"))
    except Exception:
        pass
    if used_runs:
        findings = [row for row in findings if str(row.get("run_id")) in used_runs]
    finding_by_code = {str(row.get("codigo")): row for row in findings}
    catalog = _rows(database.table("finding_catalog").select("codigo,descricao").in_("codigo", list(finding_by_code))) if finding_by_code else []
    labels = {str(row.get("codigo")): str(row.get("descricao") or "") for row in catalog}
    hashes = {str(item.get("sha256")) for finding in findings for item in (finding.get("evidencia") or []) if isinstance(item, dict) and item.get("sha256")}
    docs = _rows(database.table("documentos_cliente").select("id,cnpj,nome_arquivo,tipo,sha256,capturado_em").eq("cnpj", operation.get("cnpj"))) if hashes else []
    docs_by_hash = {str(row.get("sha256")): row for row in docs if str(row.get("sha256")) in hashes}

    def enrich(items: Any, *, kind: str = "item") -> list[dict[str, Any]]:
        output = []
        for item in items if isinstance(items, list) else []:
            value = dict(item) if isinstance(item, dict) else {"codigo": str(item)}
            code = str(value.get("codigo") or "")
            finding = _finding_projection(finding_by_code.get(code), labels, docs_by_hash)
            value["finding"] = finding
            if kind == "informativo":
                raw_value = value.get("valor")
                label = (finding or {}).get("rotulo") or labels.get(code) or _label(code)
                for text in _informative_text(code, raw_value, str(label)):
                    output.append({**value, "texto": text})
            elif kind == "alcada":
                message = value.get("mensagem") or _label(code)
                output.append({**value, "texto": f"Nível {value.get('nivel')}: {message}"})
            else:
                text = value.get("descricao") or value.get("documento_exigido") or _label(code)
                output.append({**value, "texto": str(text)})
        return output

    bloqueios = []
    for code in result.get("bloqueios") or []:
        bloqueios.append({"codigo": code, "finding": _finding_projection(finding_by_code.get(str(code)), labels, docs_by_hash)})
    oficial = _record(shadow.get("oficial"))
    return {
        "status": "OK",
        "politica": {"versao": version.get("versao"), "status": version.get("status"), "data_referencia": shadow.get("data_referencia"), "shadow_logic_version": result.get("shadow_logic_version")},
        "decisao": {"status_decisao": result.get("status_decisao"), "rating": result.get("rating"), "limite": result.get("limite_aprovado_rs"), "alcada_minima": result.get("alcada_minima", 1), "alcada_motivos": enrich(result.get("alcada_motivos"), kind="alcada"), "faltantes": [{"codigo": str(code), "texto": _MISSING_TEXT.get(str(code), _label(code))} for code in result.get("faltantes") or []], "bloqueios": bloqueios},
        "oficial": {"rating": oficial.get("rating", operation.get("rating")), "limite": oficial.get("limite_aprovado_rs", oficial.get("limite", operation.get("limite_aprovado")))},
        "condicoes_precedentes": enrich(result.get("condicoes_precedentes")),
        "ajustes": {"limite_multiplicadores": enrich(result.get("limite_multiplicadores")), "taxa_ajuste_pp": result.get("taxa_ajuste_pp", 0.0)},
        "informativos": enrich(
            [
                *(result.get("informativos") or []),
                *({"codigo": code, "valor": row.get("valor")} for code, row in finding_by_code.items() if code in _CASH_FLOW_CODES),
            ],
            kind="informativo",
        ),
    }


_CASH_FLOW_CODES = frozenset({
    "mensalidade_faturada_media", "meses_sem_fatura_12m", "glosa_pct_12m",
    "prazo_ateste_dias", "prazo_liquidacao_dias", "saldo_empenho_ano",
    "empenho_insuficiente", "cobertura_parcela", "execucao_vs_contratado",
    "prazo_pagamento_dias", "ciclo_caixa_dias", "faturas_liquidadas_sem_pagamento",
    "taxa_conciliacao_pagamentos", "pago_portal_vs_comprasnet", "antecipacao_concorrente",
    "pagamentos_contrato_12m",
})
