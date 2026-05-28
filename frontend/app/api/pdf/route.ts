import chromium from "@sparticuz/chromium";
import { NextRequest, NextResponse } from "next/server";
import puppeteer from "puppeteer-core";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";

export async function GET(req: NextRequest) {
  const { searchParams } = new URL(req.url);
  const operationId = searchParams.get("operation_id");

  if (!operationId) {
    return NextResponse.json({ error: "operation_id is required" }, { status: 400 });
  }

  const host = req.headers.get("host");
  const baseUrl = process.env.NEXT_PUBLIC_APP_URL || `https://${host}`;
  const browser = await puppeteer.launch({
    args: chromium.args,
    defaultViewport: { height: 900, width: 1280 },
    executablePath: await chromium.executablePath(),
    headless: true,
  });

  try {
    const page = await browser.newPage();
    await page.goto(`${baseUrl}/operations/${operationId}/report?print=true`, {
      timeout: 30000,
      waitUntil: "networkidle0",
    });

    await new Promise((resolve) => setTimeout(resolve, 2000));

    const pdf = await page.pdf({
      format: "A4",
      margin: { bottom: "15mm", left: "15mm", right: "15mm", top: "15mm" },
      printBackground: true,
    });

    return new NextResponse(Buffer.from(pdf), {
      headers: {
        "Content-Disposition": `attachment; filename="CreditEngine_${operationId}.pdf"`,
        "Content-Type": "application/pdf",
      },
    });
  } finally {
    await browser.close();
  }
}
