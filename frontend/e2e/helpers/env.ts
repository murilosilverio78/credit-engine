import fs from "node:fs";
import path from "node:path";

let loaded = false;

function parseEnvFile(filePath: string) {
  if (!fs.existsSync(filePath)) {
    return;
  }

  const content = fs.readFileSync(filePath, "utf-8");
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) {
      continue;
    }

    const separatorIndex = line.indexOf("=");
    if (separatorIndex === -1) {
      continue;
    }

    const key = line.slice(0, separatorIndex).trim();
    const value = line.slice(separatorIndex + 1).trim().replace(/^["']|["']$/g, "");
    if (key && process.env[key] === undefined) {
      process.env[key] = value;
    }
  }
}

export function loadE2EEnv() {
  if (loaded) {
    return;
  }

  parseEnvFile(path.join(process.cwd(), "e2e", ".env"));
  parseEnvFile(path.join(process.cwd(), ".env.local"));
  loaded = true;
}

export function env(name: string, fallback?: string) {
  loadE2EEnv();
  const value = process.env[name] || fallback;
  if (!value) {
    throw new Error(`Missing required environment variable: ${name}`);
  }
  return value;
}

export type E2ERole = "diretor" | "analista" | "gerente";

export function credentialsFor(role: E2ERole) {
  const prefix = `E2E_${role.toUpperCase()}`;
  return {
    email: env(`${prefix}_EMAIL`),
    password: env(`${prefix}_PASSWORD`),
  };
}
