import { expect, test } from "@playwright/test";

const base = (id: string, classe: string, motivo: string[] = [], acoes: Array<{ id: string; habilitada: boolean; motivo_bloqueio?: string | null }> = []) => ({
  cotacao_id: id, operation_id: id === "green" ? "op-green" : null, cnpj: "79283065000141", razao_social: "FORNECEDOR DE TESTE", contrato: "Contrato 00037/2025 · UASG 158493", aba_origem: "DOCUMENTADA",
  selo: { texto: classe, classe }, kpis: [{ rotulo: "Valor pedido", valor: 100000, formato: "BRL", nota: null }, { rotulo: "Valor enquadrado", valor: 90000, formato: "BRL", nota: null }, { rotulo: "Expira em", valor: "2026-11-25", formato: "DATA", nota: null }],
  trilha: { estagio_atual: "DOCUMENTADA", estagio_max: "QUALIFICADA", encerrada: classe === "ENCERRADA" }, parada: { classe, parou_em: null, titulo: motivo[0] || "Passou em todos os filtros gratuitos", resumo: "Resumo de teste" },
  motivos: motivo.map((titulo, index) => ({ codigo: `M${index}`, codigo_origem: `M${index}`, classe: index ? "REVISAR_DADO" : classe, chip: index ? "Revisar dado" : classe, titulo, texto: titulo, evidencias: titulo.includes("CEIS") ? [{ rotulo: "Órgão sancionador", valor: "Prefeitura Municipal de Porto Alegre (RS)", tom: "neutro" }, { rotulo: "Vigência", valor: "05/05/2027", tom: "ruim" }] : [{ rotulo: "Valor", valor: "R$ 700.000,00", tom: "bom" }], fonte: "Fixture" })),
  acoes: acoes.map((action) => ({ ...action, rotulo: action.id, nota: "Nota", exige_justificativa: false, motivo_bloqueio: action.motivo_bloqueio ?? null })),
  consultas: { titulo: "CONSULTAS EXECUTADAS", itens: [{ nome: "CEIS", coleta: "ok", resultado: classe === "VETO" ? "1 sanção ativa" : "nada consta", tom: classe === "VETO" ? "ruim" : "bom" }], nao_executados: "Nenhuma consulta paga." },
});

const cases = [
  ["apollo", base("apollo", "COMERCIAL", ["Valor acima do ticket máximo"], [{ id: "reenquadrar", habilitada: true }, { id: "arquivar", habilitada: true }])],
  ["jjx", base("jjx", "CLIENTE", ["Nenhum documento na Broadfactor"], [{ id: "pedir_documentos", habilitada: true }, { id: "consultas_gratuitas", habilitada: true }])],
  ["orbenk", base("orbenk", "VETO", ["Sanção ativa no CEIS", "Capacidade do contrato zerada"], [{ id: "recoletar", habilitada: true }, { id: "enviar_analise_completa", habilitada: false, motivo_bloqueio: "Bloqueado: existe veto ativo" }])],
  ["proclima", base("proclima", "EM_ANDAMENTO", [], [{ id: "gerar_relatorio", habilitada: true }])],
  ["green", base("green", "ENCERRADA", ["Cotação saiu da listagem"], [{ id: "abrir_analise", habilitada: true }, { id: "avisar_comercial", habilitada: true }])],
] as const;

test.describe("20 - cotação por estágio", () => {
  for (const [id, fixture] of cases) {
    test(`mostra o caso ${id} sem polling`, async ({ page }) => {
      let calls = 0;
      await page.route("**/api/v1/cotacoes/*/resumo", async (route) => { calls += 1; await route.fulfill({ contentType: "application/json", body: JSON.stringify(fixture) }); });
      await page.goto(`/cotacoes/${id}`);
      await expect(page.getByTestId("cotacao-classe")).toHaveText(fixture.parada.classe);
      if (fixture.motivos.length) await expect(page.getByTestId("motivo-0")).toContainText(fixture.motivos[0].titulo);
      if (id === "orbenk") { await expect(page.getByTestId("evidencia-0-0")).toContainText("Prefeitura Municipal de Porto Alegre"); await expect(page.getByTestId("evidencia-0-1")).toContainText("05/05/2027"); await expect(page.getByText("Bloqueado: existe veto ativo")).toBeVisible(); }
      await page.waitForTimeout(12_000);
      expect(calls).toBe(1);
    });
  }
});
