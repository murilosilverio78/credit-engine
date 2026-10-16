"""Pipeline de documentos financeiros, sem emitir achados nem alterar score.

O payload persistido foi deliberadamente minimizado. Conteudo bruto, base64 e CPF
completo nao atravessam esta camada nem entram em logs.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

import anthropic
import structlog

from app.core.config import settings
from app.core.database import supabase
from app.services.pdf_texto import Deadline, OCRUnavailableError, extract_pdf_pages, normalize_document, ocr_pdf, render_page

logger = structlog.get_logger()
SCHEMA_VERSAO = 1
FAMILIAS = ("SPED_ECD", "BALANCO_DRE", "BALANCETE", "BALANCO_ABERTURA", "FATURAMENTO", "ENDIVIDAMENTO_DECLARADO", "SCR", "INDICES", "IRPF", "CONTRATO_SOCIAL", "OUTRO")
FORBIDDEN_KEYS = {"tipo", "tipo_documento", "document_type"}
DERIVADAS = {"INDICES", "OUTRO"}


def _rows(result: Any) -> list[dict[str, Any]]:
    return list(getattr(result, "data", None) or [])


def _config() -> dict[str, Any]:
    result = supabase.table("documentos_financeiros_config").select("*").limit(1).execute()
    return (result.data or [{}])[0]


def _evidence_schema() -> dict[str, Any]:
    return {"type": "object", "properties": {"valor": {}, "unidade": {"type": ["string", "null"]}, "pagina": {"type": ["integer", "null"]}, "trecho": {"type": ["string", "null"], "maxLength": 200}, "confianca": {"type": "string", "enum": ["ALTA", "MEDIA", "BAIXA"]}, "ausente_no_documento": {"type": "boolean"}}, "required": ["valor", "unidade", "pagina", "trecho", "confianca", "ausente_no_documento"], "additionalProperties": False}


def _classification_tool() -> dict[str, Any]:
    return {"name": "classificar_documento_financeiro", "description": "Classifica exclusivamente pelo conteudo do documento.", "input_schema": {"type": "object", "properties": {"familia": {"type": "string", "enum": list(FAMILIAS)}, "ano_referencia": {"type": ["integer", "null"]}, "titular_tipo": {"type": "string", "enum": ["PJ", "PF", "NAO_IDENTIFICADO"]}, "tem_recibo_rfb": {"type": "boolean"}, "assinado_contador": {"type": "boolean"}, "crc": {"type": ["string", "null"]}, "paginas_relevantes": {"type": "array", "items": {"type": "integer"}}}, "required": ["familia", "ano_referencia", "titular_tipo", "tem_recibo_rfb", "assinado_contador", "crc", "paginas_relevantes"], "additionalProperties": False}}


def _extraction_tool(familia: str) -> dict[str, Any]:
    # O schema mantem a evidencia por campo e deixa listas financeiras abertas para
    # acomodar demonstrativos sem inventar campos ausentes.
    return {"name": "extrair_documento_financeiro", "description": "Extrai somente os campos permitidos, sempre com evidencia.", "input_schema": {"type": "object", "properties": {"familia": {"type": "string", "const": familia}, "campos": {"type": "object", "additionalProperties": _evidence_schema()}, "exercicios": {"type": "array", "items": {"type": "object"}}, "meses": {"type": "array", "items": {"type": "object"}}, "credores": {"type": "array", "items": {"type": "object"}}, "modalidades": {"type": "array", "items": {"type": "object"}}, "participacoes": {"type": "array", "items": {"type": "object"}}, "socios": {"type": "array", "items": {"type": "object"}}}, "required": ["familia", "campos", "exercicios", "meses", "credores", "modalidades", "participacoes", "socios"], "additionalProperties": False}}


def _tool_result(response: Any) -> dict[str, Any]:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and isinstance(getattr(block, "input", None), dict):
            return block.input
    raise RuntimeError("llm_sem_saida_estruturada")


def _usage(response: Any) -> tuple[int | None, int | None, float]:
    usage = getattr(response, "usage", None)
    input_tokens = getattr(usage, "input_tokens", None)
    output_tokens = getattr(usage, "output_tokens", None)
    # Conservador e auditavel; preco exato e configuracao comercial pertencem ao provedor.
    return input_tokens, output_tokens, round(((input_tokens or 0) * 0.000003) + ((output_tokens or 0) * 0.000015), 4)


def _call_tool(client: anthropic.Anthropic, tool: dict[str, Any], prompt: str) -> tuple[dict[str, Any], tuple[int | None, int | None, float]]:
    response = client.messages.create(model=settings.CLAUDE_MODEL_EXTRACTION, max_tokens=3500, temperature=0, tools=[tool], tool_choice={"type": "tool", "name": tool["name"]}, messages=[{"role": "user", "content": prompt}])
    return _tool_result(response), _usage(response)


def _safe_payload(payload: Any) -> Any:
    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        for key, value in payload.items():
            key_text = str(key)
            lowered = key_text.lower()
            if key_text in FORBIDDEN_KEYS or lowered == "cpf":
                continue
            if lowered == "cpf_final":
                safe[key_text] = re.sub(r"\D", "", str(value or ""))[-3:] or None
            else:
                safe[key_text] = _safe_payload(value)
        return safe
    if isinstance(payload, list):
        return [_safe_payload(item) for item in payload]
    if isinstance(payload, str):
        # Evita vazar CPF eventualmente devolvido pelo modelo fora do schema.
        return re.sub(r"\b\d{3}[.\s-]?\d{3}[.\s-]?\d{3}-?\d{2}\b", "[CPF_REDACTED]", payload)[:300]
    return payload


def _source_confidence(classification: dict[str, Any]) -> str:
    if classification.get("familia") == "SPED_ECD" and classification.get("tem_recibo_rfb"):
        return "ALTA"
    if classification.get("assinado_contador"):
        return "MEDIA"
    return "BAIXA"


def _extract_one(document: dict[str, Any], client: anthropic.Anthropic, config: dict[str, Any]) -> dict[str, Any]:
    sha = str(document["sha256"])
    existing = supabase.table("documentos_extracoes").select("*").eq("sha256", sha).eq("schema_versao", SCHEMA_VERSAO).maybe_single().execute().data
    if existing:
        return existing
    try:
        # Import tardio permite validar a consolidacao pura sem inicializar boto3.
        from app.services.storage_service import StorageService
        content = StorageService().download_document_at_key(str(document["storage_key"]))
        pdf, _source, _inner = normalize_document(content)
        deadline = Deadline.start(150)
        pages = extract_pdf_pages(pdf, deadline)
        if len("".join(pages)) < 400:
            pages, _total, _truncated = ocr_pdf(pdf, int(config.get("max_paginas_extracao", 40)), deadline)
        first = "".join(render_page(i + 1, text) for i, text in enumerate(pages[:3]))
        classification, cost_a = _call_tool(client, _classification_tool(), "Classifique o documento a seguir. Nao reproduza dados pessoais alem do necessario.\n" + first)
        familia = classification["familia"]
        if familia in DERIVADAS:
            row = {"sha256": sha, "familia": familia, "schema_versao": SCHEMA_VERSAO, "status": "IGNORADO_DERIVADO", "ano_referencia": classification.get("ano_referencia"), "payload": {"familia": familia, "classificacao": _safe_payload(classification)}, "modelo": settings.CLAUDE_MODEL_EXTRACTION, "tokens_entrada": cost_a[0], "tokens_saida": cost_a[1], "custo_usd": cost_a[2], "erro": None}
        else:
            page_numbers = classification.get("paginas_relevantes") or list(range(1, min(len(pages), int(config.get("max_paginas_extracao", 40))) + 1))
            text = "".join(render_page(i, pages[i - 1]) for i in page_numbers if isinstance(i, int) and 0 < i <= len(pages))
            extracted, cost_b = _call_tool(client, _extraction_tool(familia), "Extraia somente dados permitidos; nao extraia endereco, bancos, dependentes ou CPF completo.\n" + text[:120000])
            payload = _safe_payload({"familia": familia, "classificacao": classification, "extracao": extracted, "confianca_fonte": _source_confidence(classification)})
            row = {"sha256": sha, "familia": familia, "schema_versao": SCHEMA_VERSAO, "status": "EXTRAIDO", "ano_referencia": classification.get("ano_referencia"), "payload": payload, "modelo": settings.CLAUDE_MODEL_EXTRACTION, "tokens_entrada": (cost_a[0] or 0) + (cost_b[0] or 0), "tokens_saida": (cost_a[1] or 0) + (cost_b[1] or 0), "custo_usd": round(cost_a[2] + cost_b[2], 4), "erro": None}
    except OCRUnavailableError:
        row = {"sha256": sha, "familia": "OUTRO", "schema_versao": SCHEMA_VERSAO, "status": "ILEGIVEL", "payload": {}, "erro": "ocr_indisponivel"}
    except Exception as exc:
        logger.warning("documentos_financeiros.document_failed", sha256=sha, erro=exc.__class__.__name__)
        row = {"sha256": sha, "familia": "OUTRO", "schema_versao": SCHEMA_VERSAO, "status": "FALHA", "payload": {}, "erro": exc.__class__.__name__[:120]}
    return supabase.table("documentos_extracoes").upsert(row, on_conflict="sha256").execute().data[0]


def _manifest(documents: list[dict[str, Any]]) -> str:
    return hashlib.sha256("|".join(sorted(str(row["sha256"]) for row in documents if row.get("sha256"))).encode()).hexdigest()


def _consolidate(rows: list[dict[str, Any]], manifest: str) -> dict[str, Any]:
    payloads = [row.get("payload") or {} for row in rows if row.get("status") == "EXTRAIDO"]
    return {"schema_versao": SCHEMA_VERSAO, "manifest_documentos": manifest, "documentos": [{"sha256": row.get("sha256"), "familia": row.get("familia"), "status": row.get("status"), "ano_referencia": row.get("ano_referencia")} for row in rows], "demonstracoes": [], "divergencias": [], "faturamento_12m": None, "endividamento": {"fontes": [item for item in payloads if item.get("familia") in {"SCR", "ENDIVIDAMENTO_DECLARADO"}]}, "avalistas": [item for item in payloads if item.get("familia") == "IRPF"], "societario": next((item for item in payloads if item.get("familia") == "CONTRATO_SOCIAL"), None), "verificacoes": [], "indicadores": {"liquidez_corrente": None, "alavancagem": None, "margem_liquida": None, "servico_divida_sobre_faturamento": None, "salto_escala_real": None, "dependencia_publica": None, "cobertura_aval": None}}


def extract_documentos_financeiros(cnpj: str, *, operation_id: str | None = None) -> dict[str, Any]:
    documents = _rows(supabase.table("documentos_cliente").select("sha256,storage_key,dono_tipo").eq("cnpj", cnpj).eq("status", "CAPTURADO").not_.is_("sha256", "null").execute())
    unique = {str(item["sha256"]): item for item in documents if item.get("storage_key")}
    manifest = _manifest(list(unique.values()))
    if not unique:
        return _consolidate([], manifest)
    config = _config(); max_cost = float(config.get("max_custo_usd_operacao", 1)); consumed = 0.0; extracted: list[dict[str, Any]] = []
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY, timeout=180)
    try:
        for document in unique.values():
            if consumed >= max_cost:
                extracted.append({"sha256": document["sha256"], "familia": "OUTRO", "status": "FALHA", "erro": "limite_custo"}); continue
            row = _extract_one(document, client, config); consumed += float(row.get("custo_usd") or 0); extracted.append(row)
    finally:
        close = getattr(client, "close", None)
        if callable(close):
            close()
    return _consolidate(extracted, manifest)
