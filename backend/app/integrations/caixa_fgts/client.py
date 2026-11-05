"""Conservative Playwright proof of concept for the Caixa CRF portal.

This module deliberately contains no stealth, proxy rotation, fingerprinting or
challenge-bypass behavior.  A detected anti-bot page raises ``BotBlockedError``
and the caller stops.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
import logging
import os
from pathlib import Path
import re
import time
from typing import Any

from app.integrations.tst_cndt.client import solve_image_captcha, valid_cnpj

from .guardrails import FgtsGuardrails, GuardrailError, mask_cnpj
from .models import BotBlockedError, CrfFailure, CrfIrregular, CrfSuccess, FailureCode
from .parser import digits, is_invalid_captcha, parse_crf_html


logger = logging.getLogger(__name__)
CRF_URL = "https://consulta-crf.caixa.gov.br/consultacrf/pages/consultaEmpregador.jsf"
_ANTIBOT_MARKERS = (
    "imperva", "incapsula", "shieldsquare", "access denied", "captcha challenge",
    "recaptcha", "hcaptcha", "turnstile", "challenge-platform", "cf-chl", "just a moment",
)
_CNPJ_IN_TEXT = re.compile(r"\b\d{2}[.]?\d{3}[.]?\d{3}[/]?\d{4}-?\d{2}\b|\b\d{14}\b")


@dataclass(frozen=True)
class CrfSettings:
    output_dir: Path
    captcha_timeout_seconds: int = 120
    portal_timeout_ms: int = 30_000
    max_captcha_attempts: int = 2
    cache_safety_days: int = 5
    debug: bool = False


class CrfClient:
    def __init__(self, settings: CrfSettings) -> None:
        self.settings = settings
        self.settings.output_dir.mkdir(parents=True, exist_ok=True)
        self.guardrails = FgtsGuardrails(self.settings.output_dir)
        self.diagnostics: list[dict[str, Any]] = []

    def issue(self, cnpj: str, uf: str) -> dict[str, Any]:
        normalized = digits(cnpj)
        normalized_uf = str(uf or "").strip().upper()
        if not valid_cnpj(normalized):
            return CrfFailure(FailureCode.CNPJ_INVALIDO).to_dict()
        if not re.fullmatch(r"[A-Z]{2}", normalized_uf):
            return CrfFailure(FailureCode.UF_INVALIDA).to_dict()
        try:
            cached = self.guardrails.reusable(normalized, safety_days=self.settings.cache_safety_days)
            if cached:
                return {key: value for key, value in cached.items() if key != "success_date"}
            with self.guardrails.cnpj_lock(normalized):
                cached = self.guardrails.reusable(normalized, safety_days=self.settings.cache_safety_days)
                if cached:
                    return {key: value for key, value in cached.items() if key != "success_date"}
                result = self._issue_locked(normalized, normalized_uf)
                if result.get("status") == "obtida":
                    self.guardrails.remember_success(normalized, result)
                return result
        except BotBlockedError:
            raise
        except GuardrailError:
            logger.warning("caixa_fgts.guardrail_blocked", extra={"cnpj": mask_cnpj(normalized)})
            return CrfFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()
        except Exception:
            logger.exception("caixa_fgts.issue_failed", extra={"cnpj": mask_cnpj(normalized)})
            return CrfFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()

    def _issue_locked(self, cnpj: str, uf: str) -> dict[str, Any]:
        api_key = os.environ.get("TWOCAPTCHA_API_KEY")
        if not api_key:
            return CrfFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
        try:
            from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
            from playwright.sync_api import sync_playwright
        except ImportError:
            return CrfFailure(FailureCode.PORTAL_INDISPONIVEL).to_dict()
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(
                        accept_downloads=True,
                        locale="pt-BR",
                        timezone_id="America/Sao_Paulo",
                    )
                    page.set_default_timeout(self.settings.portal_timeout_ms)
                    response = page.goto(CRF_URL, wait_until="domcontentloaded")
                    self._check_antibot(page, getattr(response, "status", None))
                    self._snapshot(page, "01-abertura")
                    registration = self._visible(page, "#inscricao, input[name*='inscricao' i], input[id*='inscricao' i], input[name*='cnpj' i]")
                    state = self._visible(page, "select[name*='uf' i], select[id*='uf' i], select[name*='estado' i]")
                    captcha_image = self._visible(page, "img[id*='captcha' i], img[src*='captcha' i]")
                    captcha_answer = self._visible(page, "input[name*='captcha' i], input[id*='captcha' i], input[name*='codigo' i]")
                    submit = self._visible(page, "button:has-text('Consultar'), input[value*='Consultar' i], a:has-text('Consultar')")
                    state.select_option(label=uf)
                    for attempt in range(1, self.settings.max_captcha_attempts + 1):
                        self.guardrails.consume_rate_slot()
                        image = base64.b64encode(captcha_image.screenshot()).decode("ascii")
                        answer = solve_image_captcha(image, api_key, self.settings.captcha_timeout_seconds)
                        self._diag("captcha", tentativa=attempt, resolvido=bool(answer))
                        if not answer:
                            return CrfFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
                        registration.fill(cnpj)
                        captcha_answer.fill(answer)
                        submit.click()
                        html = self._poll_result(page)
                        self._check_antibot(page, None, html)
                        if is_invalid_captcha(html):
                            self._diag("captcha_invalido", tentativa=attempt)
                            if attempt < self.settings.max_captcha_attempts:
                                continue
                            return CrfFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
                        parsed = parse_crf_html(html, cnpj)
                        self._snapshot(page, f"02-resultado-{attempt}")
                        if parsed["situacao"] == "irregular":
                            return CrfIrregular().to_dict()
                        if parsed["situacao"] != "regular":
                            return CrfFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
                        return self._collect_certificate(page, parsed, cnpj)
                    return CrfFailure(FailureCode.CAPTCHA_NAO_RESOLVIDO).to_dict()
                finally:
                    browser.close()
        except PlaywrightTimeoutError:
            return CrfFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()

    def _collect_certificate(self, page: Any, parsed: dict[str, Any], cnpj: str) -> dict[str, Any]:
        """Capture the browser download, or print the rendered certificate to PDF."""
        try:
            certificate = page.get_by_text("Certificado de Regularidade do FGTS - CRF", exact=False)
            if certificate.count():
                certificate.first.click()
            visualizer = self._visible(page, "a:has-text('Visualizar'), button:has-text('Visualizar'), input[value*='Visualizar' i]")
        except Exception:
            return CrfFailure(FailureCode.FORMULARIO_ALTERADO).to_dict()
        captured: dict[str, Any] = {"download": None}

        def on_download(download: Any) -> None:
            captured["download"] = download

        page.on("download", on_download)
        try:
            visualizer.click()
            deadline = time.monotonic() + min(self.settings.portal_timeout_ms / 1000, 8)
            while time.monotonic() < deadline and captured["download"] is None:
                page.wait_for_timeout(200)
        finally:
            page.remove_listener("download", on_download)
        filename = self._filename(parsed, cnpj)
        target = self.settings.output_dir / filename
        download = captured["download"]
        if download is not None:
            try:
                download.save_as(str(target))
            except Exception:
                return CrfFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        else:
            self._check_antibot(page, None)
            try:
                page.pdf(path=str(target), format="A4", print_background=True)
            except Exception:
                return CrfFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        if not target.is_file() or target.stat().st_size == 0:
            return CrfFailure(FailureCode.PDF_NAO_GERADO).to_dict()
        self._snapshot(page, "03-certificado")
        return CrfSuccess(
            situacao="regular",
            numero_certificado=parsed["numero_certificado"],
            data_emissao=parsed["data_emissao"],
            data_inicio_validade=parsed["data_inicio_validade"],
            data_fim_validade=parsed["data_fim_validade"],
            cnpj_certidao=parsed["cnpj_certidao"] or cnpj,
            razao_social=parsed["razao_social"],
            filename=filename,
            storage_key=str(target.resolve()),
        ).to_dict()

    def _poll_result(self, page: Any) -> str:
        """Poll DOM content; the portal's CSP may prohibit ``unsafe-eval``."""
        deadline = time.monotonic() + self.settings.portal_timeout_ms / 1000
        while time.monotonic() < deadline:
            html = page.content()
            if is_invalid_captcha(html) or parse_crf_html(html, "").get("situacao"):
                return html
            page.wait_for_timeout(250)
        return page.content()

    def _check_antibot(self, page: Any, status: int | None, html: str | None = None) -> None:
        content = html if html is not None else page.content()
        title = ""
        try:
            title = page.title()
        except Exception:
            pass
        plain = self._mask_digits(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content)))
        if status == 403 or any(marker in f"{title} {content}".lower() for marker in _ANTIBOT_MARKERS):
            raise BotBlockedError(status=status, title=title, html_excerpt=plain)

    @staticmethod
    def _visible(page: Any, selector: str) -> Any:
        locator = page.locator(selector).first
        locator.wait_for(state="visible")
        return locator

    def _snapshot(self, page: Any, step: str) -> None:
        if not self.settings.debug:
            return
        safe_step = re.sub(r"[^a-z0-9_-]", "-", step.lower())
        try:
            page.screenshot(path=str(self.settings.output_dir / f"{safe_step}.png"), full_page=True)
            html = page.content()
            html = re.sub(r"(<input[^>]+(?:captcha|codigo)[^>]*value=[\"'])[^\"']*", r"\1***", html, flags=re.I)
            (self.settings.output_dir / f"{safe_step}.html").write_text(
                self._mask_digits(html, limit=None), encoding="utf-8"
            )
        except Exception:
            self._diag("snapshot_falhou", etapa=step)

    def _diag(self, step: str, **values: Any) -> None:
        if self.settings.debug:
            self.diagnostics.append({"etapa": step, **values})

    @staticmethod
    def _mask_digits(value: str, limit: int | None = 500) -> str:
        masked = re.sub(r"\d{8,}", "***", _CNPJ_IN_TEXT.sub("***", value))
        return masked[:limit] if limit is not None else masked

    @staticmethod
    def _filename(parsed: dict[str, Any], cnpj: str) -> str:
        number = re.sub(r"[^A-Za-z0-9_-]", "", str(parsed.get("numero_certificado") or ""))[:40]
        return f"crf_fgts_{cnpj}_{number or 'certificado'}.pdf"
