"""Extract contract terms from Broadfactor PDF documents."""

from __future__ import annotations

import io
import threading
import time
import zipfile
from dataclasses import dataclass
from datetime import date
from typing import Any

import anthropic
import structlog
from pypdf import PdfReader

from app.core.config import settings
from app.integrations.broadfactor.client import BroadfactorClient, parse_data
from app.services.contract_extraction_params_service import (
    get_contract_extraction_config,
)
from app.workers.base import BaseComponentTask
from app.workers.base import _execute_snapshot_write as _execute_with_retry


logger = structlog.get_logger()

PDF_SIGNATURE = b"%PDF"
ZIP_SIGNATURE = b"PK\x03\x04"
MIN_TEXT_CHARS = 500
OCR_DPI = 200
MAX_ZIP_FILES = 50
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
FIRST_LLM_PAGES = 8
LAST_LLM_PAGES = 2
VALID_REGIMES = {
    "CONTA_DEPOSITO_VINCULADA",
    "PAGAMENTO_FATO_GERADOR",
    "NAO_IDENTIFICADO",
}
RELEVANT_TERMS = (
    "vigencia",
    "vigência",
    "prazo",
    "valor global",
    "valor total",
    "objeto",
    "conta-deposito",
    "conta-depósito",
    "conta vinculada",
    "fato gerador",
    "anexo xii",
)

# BaseComponentTask runs synchronous handlers in worker threads. This semaphore
# prevents two CPU-heavy OCR jobs from running concurrently in the same process.
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)


class ContractDocumentError(RuntimeError):
    def __init__(self, reason: str, **details: Any):
        super().__init__(reason)
        self.reason = reason
        self.details = details


class OCRUnavailableError(RuntimeError):
    pass


@dataclass
class _Deadline:
    timeout_seconds: float
    started_at: float

    @classmethod
    def start(cls, timeout_seconds: float) -> "_Deadline":
        return cls(timeout_seconds=max(float(timeout_seconds), 1.0), started_at=time.monotonic())

    def remaining(self, stage: str) -> float:
        remaining = self.timeout_seconds - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise TimeoutError(
                f"contrato_extracao excedeu {self.timeout_seconds:.0f}s em {stage}"
            )
        return remaining


def _db(operation_id: str, action: str, request):
    return _execute_with_retry(operation_id, "contrato_extracao", action, request)


def _failure(reason: str, *, flags: list[str] | None = None, **details: Any) -> dict:
    return {
        "status_extracao": "NAO_EXTRAIDO",
        "motivo": reason,
        "flags": flags or [reason],
        **details,
    }


def _load_operation(operation_id: str) -> dict[str, Any]:
    from app.core.database import supabase

    result = _db(
        operation_id,
        "load_operation_contract_context",
        lambda: supabase.table("operations")
        .select(
            "cotacao_id,saldo_vincendo,prazo_final_meses,"
            "prazo_vincendo_meses,prazo_vincendo_indisponivel"
        )
        .eq("id", operation_id)
        .single()
        .execute(),
    )
    return result.data or {}


def _extract_pdf_from_zip(content: bytes) -> tuple[bytes, str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise ContractDocumentError("arquivo_zip_invalido") from exc

    with archive:
        entries = [item for item in archive.infolist() if not item.is_dir()]
        if len(entries) > MAX_ZIP_FILES:
            raise ContractDocumentError(
                "arquivo_zip_excede_limite",
                total_arquivos=len(entries),
            )
        total_size = sum(item.file_size for item in entries)
        if total_size > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise ContractDocumentError(
                "arquivo_zip_excede_limite",
                bytes_descompactados=total_size,
            )

        for entry in entries:
            candidate = archive.read(entry)
            if candidate.startswith(PDF_SIGNATURE):
                return candidate, entry.filename

    raise ContractDocumentError("arquivo_zip_sem_pdf")


def _normalize_document(content: bytes) -> tuple[bytes, str, str | None]:
    if content.startswith(PDF_SIGNATURE):
        return content, "PDF", None
    if content.startswith(ZIP_SIGNATURE):
        pdf, internal_name = _extract_pdf_from_zip(content)
        return pdf, "ZIP", internal_name
    raise ContractDocumentError(
        "tipo_arquivo_desconhecido",
        assinatura_hex=content[:8].hex(),
    )


def _extract_pdf_pages(content: bytes, deadline: _Deadline | None = None) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            reader.decrypt("")
        pages = []
        for index, page in enumerate(reader.pages):
            if deadline:
                deadline.remaining(f"texto_pdf_pagina_{index + 1}")
            pages.append((page.extract_text() or "").strip())
        return pages
    except Exception as exc:
        raise ContractDocumentError("pdf_invalido", error=str(exc)[:300]) from exc


def _ocr_pdf_locked(
    content: bytes,
    max_pages: int,
    deadline: _Deadline,
) -> tuple[list[str], int, bool]:
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError as exc:
        raise OCRUnavailableError("dependencias de OCR indisponiveis") from exc

    try:
        document = pdfium.PdfDocument(content)
    except Exception as exc:
        raise ContractDocumentError("pdf_invalido_para_ocr", error=str(exc)[:300]) from exc

    page_count = len(document)
    pages_to_process = min(page_count, max_pages)
    truncated = page_count > pages_to_process
    texts: list[str] = []

    try:
        for index in range(pages_to_process):
            remaining = deadline.remaining(f"ocr_pagina_{index + 1}")
            page = document[index]
            bitmap = None
            image = None
            try:
                bitmap = page.render(scale=OCR_DPI / 72)
                image = bitmap.to_pil()
                text = pytesseract.image_to_string(
                    image,
                    lang="por",
                    config="--psm 6",
                    timeout=max(1.0, min(45.0, remaining)),
                )
                texts.append((text or "").strip())
            except pytesseract.TesseractNotFoundError as exc:
                raise OCRUnavailableError("binario tesseract indisponivel") from exc
            finally:
                if image is not None:
                    image.close()
                if bitmap is not None:
                    bitmap.close()
                page.close()
    finally:
        document.close()

    return texts, page_count, truncated


def _ocr_pdf(
    content: bytes,
    max_pages: int,
    deadline: _Deadline,
) -> tuple[list[str], int, bool]:
    wait_started = time.monotonic()
    acquired = _OCR_SEMAPHORE.acquire(timeout=deadline.remaining("fila_ocr"))
    if not acquired:
        raise TimeoutError("contrato_extracao excedeu timeout aguardando fila de OCR")
    try:
        logger.info(
            "contrato_extracao.ocr_started",
            espera_segundos=round(time.monotonic() - wait_started, 3),
            max_paginas=max_pages,
        )
        return _ocr_pdf_locked(content, max_pages, deadline)
    finally:
        _OCR_SEMAPHORE.release()


def _render_page(number: int, text: str) -> str:
    return f"\n--- PAGINA {number} ---\n{text.strip()}\n"


def _select_llm_text(
    pages: list[str],
    max_chars: int,
) -> tuple[str, list[int], bool]:
    max_chars = max(int(max_chars), 1_000)
    rendered = [_render_page(index + 1, text) for index, text in enumerate(pages)]
    full_text = "".join(rendered)
    if len(full_text) <= max_chars:
        return full_text, list(range(1, len(pages) + 1)), False

    first = list(range(min(FIRST_LLM_PAGES, len(pages))))
    relevant = [
        index
        for index, text in enumerate(pages)
        if any(term in text.lower() for term in RELEVANT_TERMS)
    ]
    last_start = max(len(pages) - LAST_LLM_PAGES, 0)
    last = list(range(last_start, len(pages)))

    priority: list[int] = []
    guaranteed_first = first[:4]
    remaining_first = first[4:]
    for index in (*guaranteed_first, *relevant, *remaining_first, *last):
        if index not in priority:
            priority.append(index)

    selected_chunks: list[str] = []
    selected_pages: list[int] = []
    used = 0
    for index in priority:
        remaining = max_chars - used
        if remaining <= 0:
            break
        chunk = rendered[index]
        selected_chunks.append(chunk[:remaining])
        selected_pages.append(index + 1)
        used += min(len(chunk), remaining)

    return "".join(selected_chunks), selected_pages, True


def _tool_schema() -> dict[str, Any]:
    nullable_string = {"type": ["string", "null"]}
    return {
        "name": "registrar_contrato",
        "description": "Registra os campos extraidos do contrato administrativo.",
        "input_schema": {
            "type": "object",
            "properties": {
                "valor_global": {"type": ["number", "null"]},
                "data_inicio_vigencia": nullable_string,
                "data_fim_vigencia": nullable_string,
                "prazo_vigencia_meses": {"type": ["integer", "null"]},
                "objeto_contratual": nullable_string,
                "regime_conta_vinculada": {
                    "type": "string",
                    "enum": sorted(VALID_REGIMES),
                },
                "orgao_contratante": nullable_string,
                "numero_contrato": nullable_string,
            },
            "required": [
                "valor_global",
                "data_inicio_vigencia",
                "data_fim_vigencia",
                "prazo_vigencia_meses",
                "objeto_contratual",
                "regime_conta_vinculada",
                "orgao_contratante",
                "numero_contrato",
            ],
            "additionalProperties": False,
        },
    }


def _llm_extract(
    text: str,
    numero_contrato: str,
    deadline: _Deadline,
    client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    own_client = client is None
    if client is None:
        client = anthropic.Anthropic(
            api_key=settings.ANTHROPIC_API_KEY,
            timeout=deadline.remaining("llm_inicio"),
        )

    prompt = f"""Extraia os dados do contrato administrativo abaixo.

Regras:
- Use somente fatos presentes no texto. Nao estime valores ou datas.
- Datas devem ser YYYY-MM-DD.
- valor_global e o valor total da contratacao, nao valor mensal, unitario ou de parcela.
- O numero informado pela API e {numero_contrato!r}; confirme pelo texto quando possivel.
- CONTA_DEPOSITO_VINCULADA somente quando o contrato adotar a conta bloqueada para
  provisoes trabalhistas prevista no Anexo XII da IN 05/2017.
- PAGAMENTO_FATO_GERADOR somente quando esse regime for expressamente adotado.
- Se o texto apenas apresentar as duas alternativas sem indicar a escolhida, use
  NAO_IDENTIFICADO.
- Resuma o objeto em no maximo 500 caracteres.

TEXTO DO CONTRATO:
{text}"""

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL_EXTRACTION,
            max_tokens=1_200,
            temperature=0,
            tools=[_tool_schema()],
            tool_choice={"type": "tool", "name": "registrar_contrato"},
            messages=[{"role": "user", "content": prompt}],
        )
    finally:
        if own_client:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    deadline.remaining("llm_concluido")
    for block in response.content:
        if getattr(block, "type", None) == "tool_use":
            payload = getattr(block, "input", None)
            if isinstance(payload, dict):
                return payload
    raise ContractDocumentError("llm_sem_saida_estruturada")


def _months_between(start: date, end: date) -> int:
    if end <= start:
        return 0
    months = (end.year - start.year) * 12 + end.month - start.month
    if end.day > start.day:
        months += 1
    return max(months, 1)


def _remaining_months(end: date, today: date) -> int:
    if end <= today:
        return 0
    months = (end.year - today.year) * 12 + end.month - today.month
    if end.day < today.day:
        months -= 1
    return max(months, 0)


def _normalize_extraction(payload: dict[str, Any], numero_contrato: str) -> dict[str, Any]:
    start = parse_data(payload.get("data_inicio_vigencia"))
    end = parse_data(payload.get("data_fim_vigencia"))
    prazo = payload.get("prazo_vigencia_meses")
    if prazo is None and start and end:
        prazo = _months_between(start, end)

    regime = str(payload.get("regime_conta_vinculada") or "NAO_IDENTIFICADO")
    if regime not in VALID_REGIMES:
        regime = "NAO_IDENTIFICADO"

    valor = payload.get("valor_global")
    try:
        valor = round(float(valor), 2) if valor is not None else None
    except (TypeError, ValueError):
        valor = None

    try:
        prazo = int(prazo) if prazo is not None else None
    except (TypeError, ValueError):
        prazo = None

    def optional_text(value: Any, max_length: int | None = None) -> str | None:
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:max_length] if max_length else text

    return {
        "valor_global": valor,
        "data_inicio_vigencia": start.isoformat() if start else None,
        "data_fim_vigencia": end.isoformat() if end else None,
        "prazo_vigencia_meses": prazo,
        "objeto_contratual": optional_text(payload.get("objeto_contratual"), 500),
        "regime_conta_vinculada": regime,
        "orgao_contratante": optional_text(payload.get("orgao_contratante")),
        "numero_contrato": (
            optional_text(payload.get("numero_contrato")) or numero_contrato
        ),
    }


def _persist_term(
    operation_id: str,
    operation: dict[str, Any],
    extraction: dict[str, Any],
    today: date,
) -> tuple[int | None, int | None]:
    end = parse_data(extraction.get("data_fim_vigencia"))
    if not end:
        return None, None

    prazo_vincendo = _remaining_months(end, today)
    try:
        from app.services.eligibility_params_service import get_eligibility_config

        prazo_padrao = int(get_eligibility_config()["prazo_padrao_meses"])
    except Exception as exc:
        prazo_padrao = int(operation.get("prazo_final_meses") or 12)
        logger.warning(
            "contrato_extracao.eligibility_params_fallback",
            operation_id=operation_id,
            prazo_padrao_meses=prazo_padrao,
            error=str(exc),
        )

    prazo_final = min(prazo_padrao, prazo_vincendo)
    from app.core.database import supabase

    _db(
        operation_id,
        "save_contract_term",
        lambda: supabase.table("operations")
        .update(
            {
                "prazo_vincendo_meses": prazo_vincendo,
                "prazo_final_meses": prazo_final,
                "prazo_vincendo_indisponivel": False,
            }
        )
        .eq("id", operation_id)
        .execute(),
    )
    return prazo_vincendo, prazo_final


def _fetch(
    _cnpj: str,
    operation_id: str | None = None,
    today: date | None = None,
    broadfactor_client: BroadfactorClient | None = None,
    llm_client: anthropic.Anthropic | None = None,
) -> dict[str, Any]:
    if not operation_id:
        return _failure("operation_id_ausente")

    params = get_contract_extraction_config()
    deadline = _Deadline.start(params["timeout_total_seconds"])
    today = today or date.today()
    operation = _load_operation(operation_id)
    cotacao_id = operation.get("cotacao_id")
    if not cotacao_id:
        return _failure("cotacao_id_ausente")

    own_broadfactor_client = broadfactor_client is None
    if broadfactor_client is None:
        broadfactor_client = BroadfactorClient(
            timeout=max(1, min(30, int(deadline.remaining("broadfactor_inicio"))))
        )

    try:
        contracts = broadfactor_client.contratos_da_cotacao(cotacao_id)
        deadline.remaining("listar_contratos")
        if not contracts:
            return _failure(
                "contrato_nao_disponivel",
                cotacao_id=cotacao_id,
            )

        selected = None
        last_error: ContractDocumentError | None = None
        for contract in contracts:
            deadline.remaining("baixar_contrato")
            content = broadfactor_client.baixar_contrato(
                cotacao_id,
                contract.numero_contrato,
            )
            if not content:
                continue
            try:
                pdf, source_type, internal_name = _normalize_document(content)
                selected = (contract, pdf, source_type, internal_name)
                break
            except ContractDocumentError as exc:
                last_error = exc
                logger.warning(
                    "contrato_extracao.document_rejected",
                    operation_id=operation_id,
                    cotacao_id=cotacao_id,
                    numero_contrato=contract.numero_contrato,
                    motivo=exc.reason,
                    **exc.details,
                )

        if selected is None:
            if last_error:
                return _failure(
                    last_error.reason,
                    cotacao_id=cotacao_id,
                    **last_error.details,
                )
            return _failure(
                "download_contrato_indisponivel",
                cotacao_id=cotacao_id,
            )

        contract, pdf, source_type, internal_name = selected
        pages = _extract_pdf_pages(pdf, deadline=deadline)
        deadline.remaining("extrair_texto_pdf")
        flags: list[str] = []
        extraction_method = "CAMADA_TEXTO"
        total_pages = len(pages)
        ocr_pages = 0

        if len("".join(pages).strip()) < MIN_TEXT_CHARS:
            extraction_method = "OCR"
            try:
                pages, total_pages, ocr_truncated = _ocr_pdf(
                    pdf,
                    max_pages=max(int(params["ocr_max_pages"]), 1),
                    deadline=deadline,
                )
            except OCRUnavailableError as exc:
                logger.warning(
                    "contrato_extracao.ocr_unavailable",
                    operation_id=operation_id,
                    error=str(exc),
                )
                return _failure(
                    "documento_digitalizado_sem_ocr",
                    cotacao_id=cotacao_id,
                    numero_contrato=contract.numero_contrato,
                    tipo_arquivo=source_type,
                )
            ocr_pages = len(pages)
            if ocr_truncated:
                flags.append("documento_truncado_para_ocr")

        if len("".join(pages).strip()) < MIN_TEXT_CHARS:
            return _failure(
                "texto_insuficiente_apos_extracao",
                flags=[*flags, "texto_insuficiente_apos_extracao"],
                cotacao_id=cotacao_id,
                numero_contrato=contract.numero_contrato,
                tipo_arquivo=source_type,
                metodo_texto=extraction_method,
                total_paginas=total_pages,
                paginas_ocr=ocr_pages,
            )

        llm_text, llm_pages, llm_truncated = _select_llm_text(
            pages,
            max_chars=int(params["llm_max_chars"]),
        )
        if llm_truncated:
            flags.append("texto_truncado_para_llm")

        raw_extraction = _llm_extract(
            llm_text,
            contract.numero_contrato,
            deadline,
            client=llm_client,
        )
        extraction = _normalize_extraction(raw_extraction, contract.numero_contrato)
        deadline.remaining("persistir_prazo")
        prazo_vincendo, prazo_final = _persist_term(
            operation_id,
            operation,
            extraction,
            today,
        )
        deadline.remaining("persistencia_concluida")

        if prazo_vincendo is None:
            flags.append("prazo_vincendo_indisponivel")

        saldo_vincendo = operation.get("saldo_vincendo")
        valor_global = extraction.get("valor_global")
        ratio = None
        if saldo_vincendo not in (None, "") and valor_global:
            ratio = round(float(saldo_vincendo) / float(valor_global), 4)
            if ratio < 0.3 or ratio > 1.3:
                flags.append("saldo_vincendo_divergente_valor_global")

        result = {
            "status_extracao": "EXTRAIDO",
            "cotacao_id": cotacao_id,
            "tipo_arquivo": source_type,
            "arquivo_zip_interno": internal_name,
            "metodo_texto": extraction_method,
            "total_paginas": total_pages,
            "paginas_ocr": ocr_pages,
            "paginas_enviadas_llm": llm_pages,
            "caracteres_enviados_llm": len(llm_text),
            "prazo_vincendo_meses": prazo_vincendo,
            "prazo_final_meses": prazo_final,
            "razao_saldo_vincendo_valor_global": ratio,
            "flags": flags,
            **extraction,
        }
        logger.info(
            "contrato_extracao.completed",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            metodo_texto=extraction_method,
            total_paginas=total_pages,
            paginas_ocr=ocr_pages,
            caracteres_llm=len(llm_text),
            prazo_vincendo_meses=prazo_vincendo,
            flags=flags,
        )
        return result
    except TimeoutError as exc:
        logger.warning(
            "contrato_extracao.timeout",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            error=str(exc),
        )
        return _failure(
            "timeout_extracao_contrato",
            cotacao_id=cotacao_id,
            error=str(exc)[:300],
        )
    except ContractDocumentError as exc:
        logger.warning(
            "contrato_extracao.failed",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            motivo=exc.reason,
            **exc.details,
        )
        return _failure(
            exc.reason,
            cotacao_id=cotacao_id,
            **exc.details,
        )
    except Exception as exc:
        logger.exception(
            "contrato_extracao.unexpected_failure",
            operation_id=operation_id,
            cotacao_id=cotacao_id,
            error=str(exc),
        )
        return _failure(
            "falha_extracao_contrato",
            cotacao_id=cotacao_id,
            error=str(exc)[:300],
        )
    finally:
        if own_broadfactor_client:
            close = getattr(getattr(broadfactor_client, "_s", None), "close", None)
            if callable(close):
                close()


_task = BaseComponentTask()


def run_contrato_extracao(operation_id: str):
    return _task.execute(
        operation_id,
        component="contrato_extracao",
        handler=_fetch,
        use_cache=False,
    )
