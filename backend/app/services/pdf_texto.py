"""Leitura segura de texto de PDF/ZIP, compartilhada por extratores.

Os erros e logs desta camada nunca incluem o conteudo do documento.
"""
from __future__ import annotations

import io
import threading
import time
import zipfile
from dataclasses import dataclass

from pypdf import PdfReader

PDF_SIGNATURE = b"%PDF"
ZIP_SIGNATURE = b"PK\x03\x04"
OCR_DPI = 200
MAX_ZIP_FILES = 50
MAX_ZIP_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_OCR_SEMAPHORE = threading.BoundedSemaphore(1)


class PdfTextoError(RuntimeError):
    """Erro classificavel de normalizacao, leitura ou OCR de um documento."""


class OCRUnavailableError(RuntimeError):
    pass


@dataclass
class Deadline:
    timeout_seconds: float
    started_at: float

    @classmethod
    def start(cls, timeout_seconds: float) -> "Deadline":
        return cls(max(float(timeout_seconds), 1.0), time.monotonic())

    def remaining(self, stage: str) -> float:
        remaining = self.timeout_seconds - (time.monotonic() - self.started_at)
        if remaining <= 0:
            raise TimeoutError(f"extracao_pdf excedeu timeout em {stage}")
        return remaining


def extract_pdf_from_zip(content: bytes) -> tuple[bytes, str]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise PdfTextoError("arquivo_zip_invalido") from exc
    with archive:
        entries = [entry for entry in archive.infolist() if not entry.is_dir()]
        if len(entries) > MAX_ZIP_FILES or sum(entry.file_size for entry in entries) > MAX_ZIP_UNCOMPRESSED_BYTES:
            raise PdfTextoError("arquivo_zip_excede_limite")
        for entry in entries:
            candidate = archive.read(entry)
            if candidate.startswith(PDF_SIGNATURE):
                return candidate, entry.filename
    raise PdfTextoError("arquivo_zip_sem_pdf")


def normalize_document(content: bytes) -> tuple[bytes, str, str | None]:
    if content.startswith(PDF_SIGNATURE):
        return content, "PDF", None
    if content.startswith(ZIP_SIGNATURE):
        pdf, name = extract_pdf_from_zip(content)
        return pdf, "ZIP", name
    raise PdfTextoError("tipo_arquivo_desconhecido")


def extract_pdf_pages(content: bytes, deadline: Deadline | None = None) -> list[str]:
    try:
        reader = PdfReader(io.BytesIO(content))
        if reader.is_encrypted:
            reader.decrypt("")
        pages: list[str] = []
        for index, page in enumerate(reader.pages):
            if deadline:
                deadline.remaining(f"texto_pdf_pagina_{index + 1}")
            pages.append((page.extract_text() or "").strip())
        return pages
    except PdfTextoError:
        raise
    except Exception as exc:
        raise PdfTextoError("pdf_invalido") from exc


def ocr_pdf(content: bytes, max_pages: int, deadline: Deadline) -> tuple[list[str], int, bool]:
    try:
        import pypdfium2 as pdfium
        import pytesseract
    except ImportError as exc:
        raise OCRUnavailableError("dependencias_ocr_indisponiveis") from exc
    if not _OCR_SEMAPHORE.acquire(timeout=deadline.remaining("fila_ocr")):
        raise TimeoutError("extracao_pdf excedeu timeout aguardando OCR")
    try:
        document = pdfium.PdfDocument(content)
        try:
            page_count = len(document)
            count = min(page_count, max_pages)
            texts: list[str] = []
            for index in range(count):
                page = document[index]
                bitmap = image = None
                try:
                    bitmap = page.render(scale=OCR_DPI / 72)
                    image = bitmap.to_pil()
                    texts.append((pytesseract.image_to_string(image, lang="por", config="--psm 6", timeout=max(1, min(45, deadline.remaining(f"ocr_{index + 1}")))) or "").strip())
                finally:
                    if image is not None: image.close()
                    if bitmap is not None: bitmap.close()
                    page.close()
            return texts, page_count, page_count > count
        finally:
            document.close()
    finally:
        _OCR_SEMAPHORE.release()


def render_page(number: int, text: str) -> str:
    return f"\n--- PAGINA {number} ---\n{text.strip()}\n"


def select_pages(pages: list[str], max_chars: int, *, first_pages: int = 8, last_pages: int = 2, terms: tuple[str, ...] = ()) -> tuple[str, list[int], bool]:
    rendered = [render_page(index + 1, page) for index, page in enumerate(pages)]
    full = "".join(rendered)
    if len(full) <= max_chars:
        return full, list(range(1, len(pages) + 1)), False
    indexes = list(range(min(first_pages, len(pages))))
    indexes += [i for i, page in enumerate(pages) if any(term in page.lower() for term in terms)]
    indexes += list(range(max(0, len(pages) - last_pages), len(pages)))
    result: list[str] = []; selected: list[int] = []; used = 0
    for index in dict.fromkeys(indexes):
        chunk = rendered[index]
        if used >= max_chars: break
        result.append(chunk[:max_chars - used]); selected.append(index + 1); used += min(len(chunk), max_chars - used)
    return "".join(result), selected, True
