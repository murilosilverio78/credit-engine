"""Playwright + 2captcha CNDT collector, intentionally free of evasions."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
import logging
import os
from pathlib import Path
import time
from typing import Any

import requests

from .guardrails import GuardrailError, LocalGuardrails, mask_cnpj
from .models import CndtFailure, CndtSuccess, FailureCode
from .parser import digits, parse_cndt_text


logger = logging.getLogger(__name__)
TST_URL = "https://cndt-certidao.tst.jus.br/gerarCertidao"
_ANTIBOT_MARKERS = ("incapsula", "imperva", "shieldsquare", "access denied", "captcha challenge")


def valid_cnpj(value: str) -> bool:
    digits_only = digits(value)
    if len(digits_only) != 14 or digits_only == digits_only[0] * 14:
        return False
    weights = (
        (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
        (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2),
    )
    calculated: list[int] = []
    for index, weight in enumerate(weights):
        digits_to_use = digits_only[:12] + "".join(map(str, calculated)) if index else digits_only[:12]
        total = sum(int(number) * factor for number, factor in zip(digits_to_use, weight))
        calculated.append(0 if total % 11 < 2 else 11 - (total % 11))
    return digits_only[-2:] == "".join(map(str, calculated))


@dataclass(frozen=True)
class CndtSettings:
    output_dir: Path
    captcha_timeout_seconds: int = 120
    portal_timeout_ms: int = 30_000
    max_captcha_attempts: int = 3  # maximum external-captcha cost per run


class CndtClient:
    def __init__(self, settings: CndtSettings) -> None:
        self.settings = settings
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        self.guardrails = LocalGuardrails(self.settings.output_dir)

    def issue(self, cnpj: str) -> dict[str, Any]:
        normalized = digits(cnpj)
        if not valid_cnpj(normalized):
            return CndtFailure(FailureCode.CNPJ_INVALIDO).to_dict()
        try:
            cached = self.guardrails.reusable(normalized)
            if cached:
                return {key: value for key, value in cached.items() if key != "success_date"}
            with self.guardrails.cnpj_lock(normalized):
                cached = self.guardrails.reusable(normalized)
                if cached:
                    return {key: value for key, value in cached.items() if key != "success_date"}
                result = self._issue_locked(normalized)
                if result.get("status") == "obtida":
                    self.guardrails.remember_success(normalized, result)
                return result
        except GuardrailError:
            logger.warning("cndt.guardrail_blocked", extra={"cnpj": mask_cnpj(normalized)})
            return CndtFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()
        except Exception:
            logger.exception("cndt.issue_failed", extra={"cnpj": mask_cnpj(normalized)})
            return CndtFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()

    def _issue_locked(self, cnpj: str) -> dict[str, Any]:
        api_key = os.environ.get("TWOCAPTCHA_API_KEY")
        if not api_key:
            return CndtFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return CndtFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(accept_downloads=True)
                    page.set_default_timeout(self.settings.portal_timeout_ms)
                    try:
                        page.goto(TST_URL, wait_until="domcontentloaded")
                    except PlaywrightTimeoutError:
                        return CndtFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()
                    if self._is_antibot(page.content()):
                        return CndtFailure(FailureCode.BLOQUEIO_ANTIBOT).to_dict()
                    required = ("#cpfCnpj", "#captcha-imagem", "#captcha-resposta", "#captcha-token", "#botao-emitir", "#mensagens")
                    try:
                        for selector in required:
                            page.locator(selector).wait_for(state="attached")
                        page.locator("#captcha-imagem").wait_for(state="visible")
                        if not self._wait_for_captcha_image(page):
                            return CndtFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
                    except PlaywrightTimeoutError:
                        return CndtFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
                    for _ in range(self.settings.max_captcha_attempts):
                        self.guardrails.consume_rate_slot()
                        previous_captcha_source = page.locator("#captcha-imagem").get_attribute("src") or ""
                        captcha = self._captcha_image(page)
                        answer = self._solve_captcha(captcha, api_key)
                        if not answer:
                            return CndtFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
                        page.locator("#cpfCnpj").fill(cnpj)
                        page.locator("#captcha-resposta").fill(answer)
                        try:
                            with page.expect_response(lambda response: "/api/certidao" in response.url, timeout=self.settings.portal_timeout_ms) as pending:
                                page.locator("#botao-emitir").click()
                        except PlaywrightTimeoutError:
                            return CndtFailure(FailureCode.PDF_NAO_GERADO).to_dict()
                        response = pending.value
                        content_type = (response.headers.get("content-type") or "").lower()
                        if "application/pdf" in content_type:
                            return self._save_and_parse_pdf(response.body(), cnpj, response.headers.get("content-disposition"))
                        message = self._response_message(response)
                        if self._is_antibot(message):
                            return CndtFailure(FailureCode.BLOQUEIO_ANTIBOT).to_dict()
                        if "captcha" not in message.lower():
                            return CndtFailure(FailureCode.PDF_NAO_GERADO).to_dict()
                        if not self._wait_for_captcha_image(page, previous_captcha_source):
                            return CndtFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
                    return CndtFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
                finally:
                    browser.close()
        except PlaywrightTimeoutError:
            return CndtFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
        except requests.RequestException:
            return CndtFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
        except Exception:
            logger.exception("cndt.unexpected_failure", extra={"cnpj": mask_cnpj(cnpj)})
            return CndtFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()

    def _wait_for_captcha_image(self, page: Any, previous: str | None = None) -> bool:
        """Poll the captcha ``src`` through locators.

        The portal's Content-Security-Policy forbids ``unsafe-eval``, which
        rules out ``page.wait_for_function`` with a script string.
        """
        deadline = time.monotonic() + self.settings.portal_timeout_ms / 1000
        while time.monotonic() < deadline:
            source = page.locator("#captcha-imagem").get_attribute("src") or ""
            if source.startswith("data:image/") and (previous is None or source != previous):
                return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _is_antibot(content: str) -> bool:
        plain = content.lower()
        return any(marker in plain for marker in _ANTIBOT_MARKERS)

    @staticmethod
    def _captcha_image(page: Any) -> str:
        source = page.locator("#captcha-imagem").get_attribute("src") or ""
        if not source.startswith("data:image/") or "," not in source:
            raise ValueError("captcha_image_missing")
        return source.split(",", 1)[1]

    def _solve_captcha(self, image_base64: str, api_key: str) -> str | None:
        """Use 2captcha's documented image/base64 API; no tokens are logged."""
        try:
            submitted = requests.post("https://2captcha.com/in.php", data={
                "key": api_key, "method": "base64", "body": image_base64,
                "json": 1,
            }, timeout=20).json()
        except (requests.RequestException, ValueError):
            return None
        if submitted.get("status") != 1:
            return None
        captcha_id = submitted.get("request")
        deadline = time.monotonic() + self.settings.captcha_timeout_seconds
        while time.monotonic() < deadline:
            time.sleep(5)
            try:
                solved = requests.get("https://2captcha.com/res.php", params={
                    "key": api_key, "action": "get", "id": captcha_id, "json": 1,
                }, timeout=20).json()
            except (requests.RequestException, ValueError):
                return None
            if solved.get("status") == 1:
                return str(solved.get("request") or "")
            if solved.get("request") != "CAPCHA_NOT_READY":
                return None
        return None

    @staticmethod
    def _response_message(response: Any) -> str:
        try:
            body = response.json()
            return str(body.get("erro") or body.get("message") or "") if isinstance(body, dict) else ""
        except Exception:
            return ""

    def _save_and_parse_pdf(self, content: bytes, cnpj: str, disposition: str | None) -> dict[str, Any]:
        if not content.startswith(b"%PDF"):
            return CndtFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        try:
            from pypdf import PdfReader
            text = "\n".join(page.extract_text() or "" for page in PdfReader(BytesIO(content)).pages)
        except Exception:
            return CndtFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        parsed = parse_cndt_text(text, cnpj)
        if not parsed.get("resultado") or not parsed.get("cnpj_certidao") or not parsed.get("orgao_emissor"):
            return CndtFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        filename = self._filename(disposition, cnpj)
        target = self.settings.output_dir / filename
        target.write_bytes(content)
        return CndtSuccess(
            resultado=parsed["resultado"], data_emissao=parsed["data_emissao"],
            data_validade=parsed["data_validade"], numero_certidao=parsed["numero_certidao"],
            cnpj_certidao=parsed["cnpj_certidao"], orgao_emissor="Tribunal Superior do Trabalho",
            valida=bool(parsed["valida"]), fonte="tst", filename=filename,
            storage_key=str(target.resolve()),
        ).to_dict()

    @staticmethod
    def _filename(disposition: str | None, cnpj: str) -> str:
        # Do not trust server-provided directory components.
        if disposition:
            import re
            found = re.search(r"filename=\"?([^;\"]+)", disposition)
            if found:
                name = Path(found.group(1)).name
                if name.lower().endswith(".pdf"):
                    return name
        return f"cndt_{cnpj}.pdf"
