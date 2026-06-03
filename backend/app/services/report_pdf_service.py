from __future__ import annotations

import html
import json
import math
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from fastapi.concurrency import run_in_threadpool
from playwright.sync_api import sync_playwright

from app.utils.encoding import fix_dict_encoding


DIMENSION_ORDER = [
    "relacionamento_governamental",
    "porte_operacionalidade",
    "saude_cadastral",
    "reputacao_mercado",
]

DIMENSION_LABELS = {
    "relacionamento_governamental": "Relacionamento gov.",
    "porte_operacionalidade": "Porte / operacionalidade",
    "saude_cadastral": "Saude cadastral",
    "reputacao_mercado": "Reputacao de mercado",
}

def record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def array(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def text(value: Any, fallback: str = "-") -> str:
    if value is None or value == "":
        return fallback
    if isinstance(value, bool):
        return "Sim" if value else "Nao"
    return str(value)


def esc(value: Any, fallback: str = "-") -> str:
    return html.escape(text(value, fallback))


def money(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0
    formatted = f"{number:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {formatted}"


def number(value: Any, digits: int = 1) -> str:
    try:
        number_value = float(value or 0)
    except (TypeError, ValueError):
        number_value = 0
    return f"{number_value:.{digits}f}".replace(".", ",")


def pct_from_fraction(value: Any, digits: int = 0) -> str:
    try:
        number_value = float(value or 0) * 100
    except (TypeError, ValueError):
        number_value = 0
    return f"{number_value:.{digits}f}%".replace(".", ",")


def format_cnpj(value: Any) -> str:
    digits = re.sub(r"\D", "", text(value, ""))
    if len(digits) != 14:
        return esc(value)
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def format_date(value: Any) -> str:
    if not value:
        return "-"
    raw = str(value)
    try:
        normalized = raw.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized).strftime("%d/%m/%Y")
    except ValueError:
        return html.escape(raw)


def report_date(operation: dict[str, Any]) -> str:
    value = operation.get("completed_at") or operation.get("created_at")
    if value:
        return format_date(value)
    return datetime.now(timezone.utc).strftime("%d/%m/%Y")


def paragraph(value: Any, fallback: str = "-") -> str:
    body = esc(value, fallback).replace("\n", "<br>")
    return f'<p class="justify">{body}</p>'


def detail_grid(rows: list[tuple[str, Any]], columns: int = 2) -> str:
    items = "".join(
        f"""
        <div class="detail">
          <div class="label">{html.escape(label)}</div>
          <div class="value">{esc(value)}</div>
        </div>
        """
        for label, value in rows
    )
    return f'<div class="detail-grid cols-{columns}">{items}</div>'


def table(headers: list[str], rows: list[list[Any]], class_name: str = "") -> str:
    head = "".join(f"<th>{html.escape(header)}</th>" for header in headers)
    if not rows:
        body = f'<tr><td colspan="{len(headers)}">Nenhum registro.</td></tr>'
    else:
        body = "".join(
            "<tr>"
            + "".join(f"<td>{esc(cell)}</td>" for cell in row)
            + "</tr>"
            for row in rows
        )
    return f'<table class="{class_name}"><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>'


def snapshot_map(operation: dict[str, Any]) -> dict[str, dict[str, Any]]:
    snapshots = {}
    for item in array(operation.get("components")):
        snap = record(item)
        component = snap.get("component")
        if component:
            parsed = record(snap.get("parsed_result"))
            snap["parsed_result"] = fix_dict_encoding(parsed)
            snapshots[str(component)] = snap
    return snapshots


def metric_card(label: str, value: str, hint: str = "") -> str:
    return f"""
    <div class="metric">
      <div class="label">{html.escape(label)}</div>
      <div class="metric-value">{value}</div>
      {f'<div class="hint">{html.escape(hint)}</div>' if hint else ''}
    </div>
    """


def radar_svg(dimensions: dict[str, Any]) -> str:
    center = 145
    radius = 88

    def point(index: int, score: float = 100) -> tuple[float, float]:
        angle = -math.pi / 2 + (index * 2 * math.pi) / len(DIMENSION_ORDER)
        scaled = radius * max(0, min(score, 100)) / 100
        return center + math.cos(angle) * scaled, center + math.sin(angle) * scaled

    def points(items: list[tuple[float, float]]) -> str:
        return " ".join(f"{x:.1f},{y:.1f}" for x, y in items)

    axis = [point(index) for index in range(4)]
    data = [
        point(index, float(record(dimensions.get(key)).get("score") or 0))
        for index, key in enumerate(DIMENSION_ORDER)
    ]
    grid = "\n".join(
        f'<polygon points="{points([(center + (x - center) * scale, center + (y - center) * scale) for x, y in axis])}" />'
        for scale in [0.25, 0.5, 0.75, 1]
    )
    axes = "\n".join(
        f'<line x1="{center}" y1="{center}" x2="{x:.1f}" y2="{y:.1f}" />'
        for x, y in axis
    )
    dots = "\n".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.5" />' for x, y in data)
    label_positions = [
        (center, center - radius - 18, "middle"),
        (center + radius + 18, center + 4, "start"),
        (center, center + radius + 28, "middle"),
        (center - radius - 18, center + 4, "end"),
    ]
    labels = "\n".join(
        f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}">{html.escape(DIMENSION_LABELS[key])}</text>'
        for (x, y, anchor), key in zip(label_positions, DIMENSION_ORDER)
    )
    return f"""
    <svg class="radar" viewBox="0 0 290 290" role="img" aria-label="Radar 4D">
      <g class="radar-grid">{grid}{axes}</g>
      <polygon class="radar-area" points="{points(data)}" />
      <g class="radar-dots">{dots}</g>
      <g class="radar-labels">{labels}</g>
    </svg>
    """


def cover_section(operation: dict[str, Any], snapshots: dict[str, dict[str, Any]], engine: dict[str, Any]) -> str:
    company = record(snapshots.get("brasil_api", {}).get("parsed_result"))
    legal = record(snapshots.get("pessoa_juridica", {}).get("parsed_result"))
    contracts = record(snapshots.get("contratos", {}).get("parsed_result"))
    tax_regimes = array(company.get("regime_tributario"))
    partners = array(company.get("qsa"))
    rows = [
        ("Razao social", company.get("razao_social") or operation.get("razao_social")),
        ("CNPJ", format_cnpj(company.get("cnpj") or operation.get("cnpj"))),
        ("Porte", company.get("porte")),
        ("Natureza juridica", company.get("natureza_juridica")),
        ("Abertura", format_date(company.get("data_abertura"))),
        ("Capital social", money(company.get("capital_social"))),
        ("Situacao cadastral", company.get("situacao_cadastral")),
        ("Municipio/UF", f"{text(company.get('municipio'))} / {text(company.get('uf'))}"),
        ("Simples Nacional", company.get("opcao_simples")),
        ("MEI", company.get("opcao_mei")),
        ("Portal Transparencia", "sem registro" if legal.get("erro") == "sem_registro" else "consultado"),
    ]
    regime_rows = [[item.get("ano"), item.get("forma")] for item in map(record, tax_regimes)]
    partner_rows = [[item.get("nome"), item.get("qualificacao"), format_date(item.get("data_entrada"))] for item in map(record, partners)]
    metrics = "".join(
        [
            metric_card("Score", f"{number(operation.get('score') or engine.get('score'), 1)} / 100"),
            metric_card("Taxa a.m.", pct_from_fraction(operation.get("taxa_sugerida"), 2)),
            metric_card("Limite %", pct_from_fraction(engine.get("limite_sugerido_pct_contrato"), 0)),
            metric_card(
                "Contratos ativos",
                text(contracts.get("contratos_ativos"), "0"),
                money(contracts.get("valor_total_ativo")),
            ),
        ]
    )
    rating = esc(operation.get("rating") or engine.get("rating"), "-")
    return f"""
    <section class="cover page-section">
      <div class="eyebrow">Relatorio de credito</div>
      <h1>1. Capa / resumo</h1>
      <div class="cover-head">
        <div>
          <div class="company-name">{esc(company.get("razao_social") or operation.get("razao_social"), "Empresa nao identificada")}</div>
          <p>{format_cnpj(company.get("cnpj") or operation.get("cnpj"))}</p>
        </div>
        <div class="rating">Rating {rating}</div>
      </div>
      {detail_grid(rows, 3)}
      <h2>Indicadores</h2>
      <div class="metrics">{metrics}</div>
      <div class="split">
        <div>
          <h2>Regime tributario por ano</h2>
          {table(["Ano", "Forma"], regime_rows)}
        </div>
        <div>
          <h2>Socios</h2>
          {table(["Nome", "Qualificacao", "Entrada"], partner_rows)}
        </div>
      </div>
    </section>
    """


def scorecard_section(engine: dict[str, Any]) -> str:
    dimensions = record(engine.get("dimensoes"))
    rows = []
    for key in DIMENSION_ORDER:
        item = record(dimensions.get(key))
        rows.append([
            DIMENSION_LABELS[key],
            number(item.get("score"), 1),
            item.get("nivel"),
            pct_from_fraction(item.get("peso"), 0),
            number(item.get("score_contrib"), 2),
        ])
    return f"""
    <section class="page-section avoid-break">
      <h1>2. Scorecard</h1>
      <div class="scorecard">
        <div>{radar_svg(dimensions)}</div>
        <div>{table(["Dimensao", "Nota", "Nivel", "Peso", "Contrib."], rows)}</div>
      </div>
    </section>
    """


def parecer_section(engine: dict[str, Any]) -> str:
    dimensions = record(engine.get("dimensoes"))
    blocks = []
    for key in DIMENSION_ORDER:
        item = record(dimensions.get(key))
        fatores = "; ".join(text(value) for value in array(item.get("fatores"))) or "-"
        flags = ", ".join(text(value) for value in array(item.get("flags"))) or "-"
        blocks.append(
            f"""
            <article class="dimension-block avoid-break">
              <h2>{html.escape(DIMENSION_LABELS[key])}</h2>
              <div class="mini-grid">
                <span>Nota {number(item.get("score"), 1)}</span>
                <span>Nivel {esc(item.get("nivel"))}</span>
                <span>Peso {pct_from_fraction(item.get("peso"), 0)}</span>
                <span>Contrib. {number(item.get("score_contrib"), 2)}</span>
              </div>
              {paragraph(item.get("relatorio") or item.get("justificativa"), "Relatorio nao disponivel.")}
              <p><strong>Fatores:</strong> {html.escape(fatores)}</p>
              <p><strong>Flags:</strong> {html.escape(flags)}</p>
            </article>
            """
        )
    return f"""
    <section class="page-section">
      <h1>3. Parecer completo</h1>
      {''.join(blocks)}
    </section>
    """


def regularity_section(engine: dict[str, Any]) -> str:
    regularity = record(engine.get("regularidade"))
    fator = regularity.get("fator", 1)
    merit = engine.get("merit") or engine.get("score")
    haircuts = [
        [item.get("certidao"), item.get("estado"), number(item.get("haircut"), 2)]
        for item in map(record, array(regularity.get("haircuts")))
    ]
    return f"""
    <section class="page-section avoid-break">
      <h1>4. Regularidade</h1>
      <div class="calc">Merito {number(merit, 1)} x fator {number(fator, 2)} = score final {number(engine.get("score"), 1)}</div>
      {table(["Certidao", "Estado", "Haircut"], haircuts)}
    </section>
    """


def pricing_section(operation: dict[str, Any]) -> str:
    breakdown = record(operation.get("taxa_breakdown"))
    details = record(breakdown.get("detalhes"))
    operacional = (
        float(breakdown.get("taxa_adm_am") or 0)
        + float(breakdown.get("bancarizacao_am") or 0)
        + float(breakdown.get("orig_am") or 0)
    )
    monthly_rows = [
        ["Funding", pct_from_fraction(breakdown.get("funding_ponderado_am"), 2)],
        ["EL", pct_from_fraction(breakdown.get("el_mensal"), 2)],
        ["Bond", pct_from_fraction(breakdown.get("custo_bond_am"), 2)],
        ["SERPRO", pct_from_fraction(breakdown.get("serpro_am"), 2)],
        ["Operacional", pct_from_fraction(operacional, 2)],
        ["Margem", pct_from_fraction(breakdown.get("sub_am"), 2)],
    ]
    value_rows = [
        ["Funding", money(details.get("custo_funding_rs"))],
        ["Risco", money(details.get("custo_risco_rs"))],
        ["Bond", money(details.get("custo_bond_rs"))],
        ["Operacional", money(details.get("custo_operacional_rs"))],
        ["Subordinado", money(details.get("custo_sub_rs"))],
        ["Receita total", money(details.get("total_receita_rs"))],
    ]
    return f"""
    <section class="page-section avoid-break">
      <h1>5. Detalhamento da taxa</h1>
      <div class="split">
        <div>{table(["Componente mensal", "Taxa"], monthly_rows)}</div>
        <div>{table(["Valor estimado", "R$"], value_rows)}</div>
      </div>
    </section>
    """


def contracts_annex(snapshots: dict[str, dict[str, Any]]) -> str:
    result = record(snapshots.get("contratos", {}).get("parsed_result"))
    contracts = [record(item) for item in array(result.get("contratos_detalhe"))]
    rows = [
        [
            item.get("numero"),
            item.get("orgao"),
            money(item.get("valor_inicial")),
            item.get("situacao"),
            f"{format_date(item.get('data_inicio'))} a {format_date(item.get('data_fim'))}",
        ]
        for item in contracts
    ]
    totals = detail_grid(
        [
            ("Total contratos", result.get("total_contratos")),
            ("Ativos", result.get("contratos_ativos")),
            ("Encerrados", result.get("contratos_encerrados")),
            ("Valor total ativo", money(result.get("valor_total_ativo"))),
            ("Valor historico", money(result.get("valor_total_historico"))),
        ],
        5,
    )
    return f"""
    <section class="page-section">
      <h1>6. Anexo - contratos</h1>
      {totals}
      {table(["Numero", "Orgao", "Valor", "Status", "Vigencia"], rows, "compact")}
    </section>
    """


def resources_annex(snapshots: dict[str, dict[str, Any]]) -> str:
    result = record(snapshots.get("recursos_recebidos", {}).get("parsed_result"))
    resources = [record(item) for item in array(result.get("recursos_detalhe"))]
    agency_totals: dict[str, float] = {}
    for item in resources:
        agency = text(item.get("orgao"), "")
        agency_totals[agency] = agency_totals.get(agency, 0) + float(item.get("valor") or 0)
    top_agencies = sorted(agency_totals.items(), key=lambda item: item[1], reverse=True)[:10]
    return f"""
    <section class="page-section">
      <h1>7. Anexo - recursos recebidos</h1>
      {detail_grid([
          ("Total recebido", money(result.get("valor_total_recebido"))),
          ("Periodo", f"{text(result.get('periodo_inicio'))} a {text(result.get('periodo_fim'))}"),
          ("Registros", result.get("total_registros")),
      ], 3)}
      <h2>Por ano</h2>
      {table(["Ano", "Valor"], [[year, money(value)] for year, value in record(result.get("valor_por_ano")).items()])}
      <h2>Top orgaos pagadores</h2>
      {table(["Orgao", "Valor"], [[agency, money(value)] for agency, value in top_agencies])}
    </section>
    """


def generic_record_table(data: dict[str, Any]) -> str:
    rows = []
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            rendered = json.dumps(value, ensure_ascii=False)
        else:
            rendered = text(value)
        rows.append([key, rendered])
    return table(["Campo", "Valor"], rows, "compact")


def sanctions_and_docs_annex(snapshots: dict[str, dict[str, Any]]) -> str:
    legal = record(snapshots.get("pessoa_juridica", {}).get("parsed_result"))
    parts = [
        "<section class='page-section'>",
        "<h1>8. Anexo - cadastro complementar, certidoes e reputacao</h1>",
        "<h2>pessoa_juridica</h2>",
        generic_record_table(legal),
    ]
    for component in ["ceis", "cnep", "cepim", "acordos_leniencia"]:
        result = record(snapshots.get(component, {}).get("parsed_result"))
        records = [record(item) for item in array(result.get("registros") or result.get("acordos"))]
        parts.append(f"<h2>{component}</h2>")
        if records:
            columns = list(records[0].keys())
            parts.append(table(columns, [[item.get(column) for column in columns] for item in records], "compact"))
        else:
            parts.append("<p>Nenhum registro.</p>")
    for component in ["cnd_federal", "cndt_tst", "fgts"]:
        result = record(snapshots.get(component, {}).get("parsed_result"))
        parts.append(f"<h2>{component}</h2>")
        parts.append(
            detail_grid(
                [
                    ("Resultado", result.get("resultado") or result.get("status")),
                    ("Emissao", format_date(result.get("data_emissao"))),
                    ("Validade", format_date(result.get("data_validade"))),
                    ("Orgao", result.get("orgao_emissor") or result.get("orgao")),
                ],
                4,
            )
        )
    web = record(snapshots.get("web_research", {}).get("parsed_result"))
    parts.append("<h2>web_research</h2>")
    parts.append(
        detail_grid(
            [
                ("Nivel de risco", web.get("nivel_risco")),
                ("Nivel", web.get("nivel")),
                ("Score reputacao", web.get("score_reputacao")),
            ],
            3,
        )
    )
    parts.append(paragraph(web.get("resumo"), "Resumo nao disponivel."))
    parts.append(f"<p><strong>Alertas:</strong> {esc('; '.join(text(item) for item in array(web.get('alertas'))), '-')}</p>")
    parts.append("</section>")
    return "".join(parts)


def styles() -> str:
    return """
    <style>
      @page { size: A4; margin: 92px 42px 74px; }
      * { box-sizing: border-box; }
      body {
        margin: 0;
        color: #172033;
        font-family: Inter, Arial, sans-serif;
        font-size: 11px;
        line-height: 1.45;
      }
      h1 { margin: 0 0 12px; color: #0f2742; font-size: 19px; line-height: 1.2; }
      h2 {
        margin: 16px 0 8px;
        color: #47606f;
        font-size: 10px;
        letter-spacing: .05em;
        text-transform: uppercase;
      }
      p { margin: 0 0 8px; }
      .justify { text-align: justify; }
      .page-section { break-after: page; }
      .page-section:last-child { break-after: auto; }
      .avoid-break, .detail, .metric, .dimension-block, tr { break-inside: avoid; page-break-inside: avoid; }
      .eyebrow { color: #639922; font-size: 11px; font-weight: 700; letter-spacing: .08em; text-transform: uppercase; }
      .cover-head { align-items: flex-start; display: flex; justify-content: space-between; gap: 18px; margin: 10px 0 18px; }
      .company-name { color: #0f2742; font-size: 27px; font-weight: 700; line-height: 1.15; margin-bottom: 4px; }
      .rating {
        background: #0f2742;
        border-radius: 6px;
        color: white;
        font-size: 22px;
        font-weight: 700;
        padding: 12px 16px;
        white-space: nowrap;
      }
      .detail-grid { display: grid; gap: 8px; margin: 10px 0 14px; }
      .cols-2 { grid-template-columns: repeat(2, 1fr); }
      .cols-3 { grid-template-columns: repeat(3, 1fr); }
      .cols-4 { grid-template-columns: repeat(4, 1fr); }
      .cols-5 { grid-template-columns: repeat(5, 1fr); }
      .detail, .metric {
        background: #f5f7f8;
        border: 1px solid #dce3e8;
        border-radius: 6px;
        padding: 8px 9px;
      }
      .label { color: #647481; font-size: 9px; margin-bottom: 3px; text-transform: uppercase; }
      .value { font-weight: 600; overflow-wrap: anywhere; }
      .metrics { display: grid; gap: 8px; grid-template-columns: repeat(4, 1fr); margin-bottom: 14px; }
      .metric-value { color: #0f2742; font-size: 20px; font-weight: 700; }
      .hint { color: #647481; font-size: 10px; margin-top: 2px; }
      .split { display: grid; gap: 14px; grid-template-columns: 1fr 1fr; }
      table { border-collapse: collapse; margin: 7px 0 12px; width: 100%; }
      th {
        background: #e9eff2;
        color: #334b5e;
        font-size: 9px;
        padding: 6px;
        text-align: left;
        text-transform: uppercase;
      }
      td { border-bottom: 1px solid #e2e8ee; padding: 6px; vertical-align: top; }
      .compact td, .compact th { font-size: 9px; padding: 4px 5px; }
      .scorecard { align-items: center; display: grid; gap: 20px; grid-template-columns: 300px 1fr; }
      .radar { height: 290px; width: 290px; }
      .radar-grid polygon, .radar-grid line { fill: none; stroke: #d5dde3; stroke-width: 1; }
      .radar-area { fill: rgba(99,153,34,.18); stroke: #639922; stroke-linejoin: round; stroke-width: 2.4; }
      .radar-dots circle { fill: #639922; }
      .radar-labels text { fill: #47606f; font-size: 10px; font-weight: 600; }
      .dimension-block { border-left: 3px solid #639922; margin-bottom: 14px; padding: 6px 0 4px 12px; }
      .mini-grid { color: #647481; display: flex; flex-wrap: wrap; gap: 10px; margin-bottom: 7px; }
      .calc { background: #eef5e8; border-radius: 6px; color: #244f10; font-size: 15px; font-weight: 700; margin-bottom: 12px; padding: 10px 12px; }
    </style>
    """


def document_html(operation: dict[str, Any]) -> tuple[str, str, str]:
    operation = fix_dict_encoding(operation)
    snapshots = snapshot_map(operation)
    engine = record(snapshots.get("score_engine", {}).get("parsed_result"))
    company = record(snapshots.get("brasil_api", {}).get("parsed_result"))
    op_number = text(operation.get("id"))
    date = report_date(operation)
    body = "".join(
        [
            cover_section(operation, snapshots, engine),
            scorecard_section(engine),
            parecer_section(engine),
            regularity_section(engine),
            pricing_section(operation),
            contracts_annex(snapshots),
            resources_annex(snapshots),
            sanctions_and_docs_annex(snapshots),
        ]
    )
    title = f"Credit Engine - {text(company.get('razao_social') or operation.get('cnpj'))}"
    html_doc = f"""
    <!doctype html>
    <html lang="pt-BR">
      <head>
        <meta charset="utf-8" />
        <title>{html.escape(title)}</title>
        {styles()}
      </head>
      <body>{body}</body>
    </html>
    """
    header = f"""
    <div style="font-family: Arial, sans-serif; font-size: 9px; width: 100%; padding: 0 42px;">
      <div style="border-bottom: 1px solid #cfd8df; color: #172033; display: flex; justify-content: space-between; padding-bottom: 8px;">
        <span><strong>Credit Engine</strong> / AntecipaGov</span>
        <span>Operacao {html.escape(op_number)} &nbsp;|&nbsp; Confidencial</span>
      </div>
    </div>
    """
    footer = f"""
    <div style="font-family: Arial, sans-serif; font-size: 8px; width: 100%; padding: 0 42px;">
      <div style="border-top: 1px solid #cfd8df; color: #647481; display: flex; justify-content: space-between; padding-top: 8px;">
        <span>Gerado em {html.escape(date)}</span>
        <span>Pagina <span class="pageNumber"></span> de <span class="totalPages"></span> · Credit Engine · Confidencial</span>
      </div>
    </div>
    """
    return html_doc, header, footer


class ReportPdfService:
    async def render_operation_pdf(self, operation_id: str) -> bytes:
        from app.services.operation_service import OperationService

        operation = await OperationService().get_with_snapshots(operation_id)
        if not operation:
            raise HTTPException(status_code=404, detail="Operacao nao encontrada")
        return await run_in_threadpool(self._render_pdf, operation)

    def _render_pdf(self, operation: dict[str, Any]) -> bytes:
        html_doc, header, footer = document_html(operation)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = browser.new_page()
                page.set_content(html_doc, wait_until="load")
                return page.pdf(
                    format="A4",
                    print_background=True,
                    display_header_footer=True,
                    header_template=header,
                    footer_template=footer,
                    margin={"top": "92px", "right": "42px", "bottom": "74px", "left": "42px"},
                )
            finally:
                browser.close()
