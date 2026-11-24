import { Bell, CircleCheck, CircleX, LayoutGrid } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

export const dynamic = "force-dynamic";

type Salud = { status: string; checks: Record<string, string> };

async function consultarSalud(): Promise<Salud | null> {
  // Dentro de Docker el panel habla con la API por la red interna.
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  try {
    const resp = await fetch(`${base}/health`, { cache: "no-store" });
    return (await resp.json()) as Salud;
  } catch {
    return null;
  }
}

function Chequeo({ nombre, bien }: { nombre: string; bien: boolean }) {
  const Icono = bien ? CircleCheck : CircleX;
  return (
    <li className="flex items-center gap-2">
      <Icono aria-hidden className={bien ? "size-4 text-primary" : "size-4 text-destructive"} />
      {nombre}: {bien ? "bien" : "con fallas"}
    </li>
  );
}

export default async function Inicio() {
  const salud = await consultarSalud();
  const todoBien = salud?.status === "ok";

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="text-3xl font-semibold sm:text-4xl">Bitácora de Café</h1>
      <p className="mt-2 max-w-xl text-muted-foreground">
        Lo que los caficultores cuentan por WhatsApp, ordenado lote por lote.
      </p>
      <div className="mt-6 flex flex-wrap gap-3">
        <Button asChild size="lg">
          <Link href="/lotes">
            <LayoutGrid aria-hidden />
            Ver los lotes
          </Link>
        </Button>
        <Button asChild size="lg" variant="outline">
          <Link href="/alertas">
            <Bell aria-hidden />
            Ver las alertas
          </Link>
        </Button>
      </div>

      <Card className="mt-10 max-w-md">
        <CardHeader>
          <CardTitle className="font-serif">Estado del sistema</CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          {salud === null ? (
            <p className="text-destructive">No pudimos conectarnos con el servidor.</p>
          ) : (
            <>
              <p className={todoBien ? "font-medium text-primary" : "font-medium text-destructive"}>
                {todoBien ? "Todo está funcionando." : "Hay un problema en el sistema."}
              </p>
              <ul className="mt-3 space-y-1 text-muted-foreground">
                <Chequeo nombre="Base de datos" bien={salud.checks.db === "ok"} />
                <Chequeo nombre="Cola de tareas" bien={salud.checks.redis === "ok"} />
              </ul>
            </>
          )}
        </CardContent>
      </Card>
    </main>
  );
}
