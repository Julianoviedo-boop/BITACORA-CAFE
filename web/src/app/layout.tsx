import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Bitácora de Café",
  description: "Historia y trazabilidad de sus lotes de café",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="es-CO">
      <body className="min-h-screen bg-stone-50 text-stone-900 antialiased">
        <header className="border-b border-stone-200 bg-white">
          <nav className="mx-auto flex max-w-3xl items-center gap-6 px-4 py-3 sm:px-8">
            <Link href="/" className="font-bold text-amber-900">
              Bitácora de Café
            </Link>
            <Link href="/lotes" className="text-stone-700 hover:text-amber-800">
              Lotes
            </Link>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
