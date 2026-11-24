"use client";

import { Bell, Coffee, LayoutGrid } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const ENLACES = [
  { href: "/lotes", texto: "Lotes", icono: LayoutGrid },
  { href: "/alertas", texto: "Alertas", icono: Bell },
];

export function Navegacion() {
  const ruta = usePathname();
  return (
    <header className="sticky top-0 z-10 border-b border-primary/20 bg-primary text-primary-foreground">
      <nav className="mx-auto flex max-w-5xl items-center gap-1 px-4 py-2.5 sm:px-6">
        <Link href="/" className="mr-auto flex items-center gap-2 py-1 font-serif text-lg font-semibold">
          <Coffee aria-hidden className="size-5 text-terracota-suave" />
          <span>Bitácora de Café</span>
        </Link>
        {ENLACES.map(({ href, texto, icono: Icono }) => {
          const activo = ruta === href || ruta.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              aria-current={activo ? "page" : undefined}
              className={cn(
                "flex items-center gap-1.5 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                activo ? "bg-primary-foreground/15" : "text-primary-foreground/80 hover:bg-primary-foreground/10",
              )}
            >
              <Icono aria-hidden className="size-4" />
              {texto}
            </Link>
          );
        })}
      </nav>
    </header>
  );
}
