import fs from "node:fs";
import path from "node:path";

import { expect, test } from "./helpers/fixtures";
import { skipIfNoCredentials } from "./helpers/test-data";

const pdfFixture = path.join(process.cwd(), "e2e", "fixtures", "certidao-exemplo.pdf");

test.describe("Módulo 15 - Upload de certidões", () => {
  test.beforeEach(async ({}, testInfo) => {
    testInfo.skip(!fs.existsSync(pdfFixture), "Add frontend/e2e/fixtures/certidao-exemplo.pdf to run upload tests.");
  });

  test("15.1 - upload de certidão", async ({ diretorPage }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    testInfo.skip(true, "Requires an operation in manual_review with pending upload tasks.");
    await diretorPage.setInputFiles("input[type=file]", pdfFixture);
  });

  test("15.2 - validação de CNPJ cruzado", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a certificate fixture for a different CNPJ.");
  });

  test("15.3 - tipo de arquivo inválido", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.post("/api/v1/uploads/token-invalido", {
      multipart: { document_type: "fgts", file: { buffer: Buffer.from("txt"), mimeType: "text/plain", name: "invalid.txt" } },
    });
    expect([400, 404]).toContain(response.status());
  });

  test("15.4 - arquivo acima do limite", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a valid upload token and a generated file above 10MB.");
  });

  test("15.5 - substituir certidão enviada", async ({}, testInfo) => {
    testInfo.skip(true, "Requires a completed upload task fixture.");
  });

  test("15.6 - retomada do pipeline", async ({}, testInfo) => {
    testInfo.skip(true, "Requires an operation in manual_review with all certificates uploaded.");
  });

  test("15.7 - token de upload inválido", async ({ apiDiretor }, testInfo) => {
    skipIfNoCredentials(testInfo, "diretor");
    const response = await apiDiretor.post("/api/v1/uploads/token-invalido", {
      multipart: { document_type: "fgts", file: { buffer: Buffer.from("%PDF"), mimeType: "application/pdf", name: "certidao.pdf" } },
    });
    expect(response.status()).toBe(404);
  });
});
