import type { Metadata } from "next";
import type { ReactNode } from "react";
import { GeistSans } from "geist/font/sans";

import { Providers } from "@/app/providers";

import "./globals.css";

export const metadata: Metadata = {
  title: "AntecipaGov Credit Engine",
  description: "Admin UI para analise de credito e revisao de alcadas.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: ReactNode }>) {
  return (
    <html lang="pt-BR">
      <body className={`${GeistSans.className} min-h-dvh`}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
