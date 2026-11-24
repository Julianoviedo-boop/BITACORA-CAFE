import type { Metadata, Viewport } from "next";
import { Fraunces, Source_Sans_3 } from "next/font/google";
import "./globals.css";

// Títulos con serifa (de libreta de campo) y texto muy legible en celular.
const titulos = Fraunces({ subsets: ["latin"], variable: "--font-titulos", display: "swap" });
const texto = Source_Sans_3({ subsets: ["latin"], variable: "--font-texto", display: "swap" });

export const metadata: Metadata = {
  title: { default: "Bitácora de Café", template: "%s · Bitácora de Café" },
  description: "Historia y trazabilidad de sus lotes de café",
};

export const viewport: Viewport = { themeColor: "#2f5b43" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es-CO" className={`${titulos.variable} ${texto.variable}`}>
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
