import type { Alcada, UserRole } from "@/lib/types";

export interface SessionUser {
  id: string;
  email: string;
  name: string;
  role: UserRole;
  alcada: Alcada;
}

export interface SessionPayload extends SessionUser {
  exp: number;
  session_token?: string;
}

const encoder = new TextEncoder();

function base64UrlEncode(input: string | Uint8Array) {
  const bytes =
    typeof input === "string" ? encoder.encode(input) : input;
  let binary = "";
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  const base64 =
    typeof btoa === "function"
      ? btoa(binary)
      : Buffer.from(bytes).toString("base64");
  return base64.replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

function base64UrlDecode(input: string) {
  const base64 = input.replace(/-/g, "+").replace(/_/g, "/");
  const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
  if (typeof atob === "function") {
    return decodeURIComponent(
      Array.from(atob(padded))
        .map((char) => `%${char.charCodeAt(0).toString(16).padStart(2, "0")}`)
        .join(""),
    );
  }
  return Buffer.from(padded, "base64").toString("utf-8");
}

function secret() {
  return (
    process.env.NEXTAUTH_SECRET ||
    process.env.SECRET_KEY ||
    "credit-engine-dev-secret"
  );
}

async function hmac(data: string) {
  const key = await crypto.subtle.importKey(
    "raw",
    encoder.encode(secret()),
    { hash: "SHA-256", name: "HMAC" },
    false,
    ["sign"],
  );
  return new Uint8Array(await crypto.subtle.sign("HMAC", key, encoder.encode(data)));
}

export async function signSessionJwt(payload: Omit<SessionPayload, "exp">, maxAgeSeconds = 60 * 60 * 24 * 7) {
  const header = base64UrlEncode(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = base64UrlEncode(
    JSON.stringify({
      ...payload,
      exp: Math.floor(Date.now() / 1000) + maxAgeSeconds,
    }),
  );
  const unsigned = `${header}.${body}`;
  const signature = base64UrlEncode(await hmac(unsigned));
  return `${unsigned}.${signature}`;
}

export async function verifySessionJwt(token?: string | null): Promise<SessionPayload | null> {
  if (!token) {
    return null;
  }

  const [header, body, signature] = token.split(".");
  if (!header || !body || !signature) {
    return null;
  }

  const expected = base64UrlEncode(await hmac(`${header}.${body}`));
  if (expected !== signature) {
    return null;
  }

  try {
    const payload = JSON.parse(base64UrlDecode(body)) as SessionPayload;
    if (!payload.exp || payload.exp < Math.floor(Date.now() / 1000)) {
      return null;
    }
    return payload;
  } catch {
    return null;
  }
}

export function roleToAlcada(role: UserRole): Alcada {
  if (role === "diretor") {
    return "diretor";
  }
  if (role === "gerente") {
    return "gerente";
  }
  return "analista";
}
