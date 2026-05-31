import { test as base, type APIRequestContext, type Page } from "@playwright/test";

import { apiContext } from "./api";
import { storageStateFor } from "./auth";
import { env } from "./env";

type E2EFixtures = {
  analistaPage: Page;
  apiAnalista: APIRequestContext;
  apiDiretor: APIRequestContext;
  diretorPage: Page;
  gerentePage: Page;
};

export const test = base.extend<E2EFixtures>({
  analistaPage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: env("E2E_BASE_URL", "http://localhost:3000"),
      storageState: await storageStateFor("analista"),
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  apiAnalista: async ({}, use) => {
    const context = await apiContext("analista");
    await use(context);
    await context.dispose();
  },
  apiDiretor: async ({}, use) => {
    const context = await apiContext("diretor");
    await use(context);
    await context.dispose();
  },
  diretorPage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: env("E2E_BASE_URL", "http://localhost:3000"),
      storageState: await storageStateFor("diretor"),
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
  gerentePage: async ({ browser }, use) => {
    const context = await browser.newContext({
      baseURL: env("E2E_BASE_URL", "http://localhost:3000"),
      storageState: await storageStateFor("gerente"),
    });
    const page = await context.newPage();
    await use(page);
    await context.close();
  },
});

export { expect } from "@playwright/test";
