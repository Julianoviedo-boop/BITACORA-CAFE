import { Clock, Send, Thermometer, TriangleAlert } from "lucide-react";
import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import type { Alerta } from "@/lib/api";

export function ListaAlertas({ alertas, conLote = true }: { alertas: Alerta[]; conLote?: boolean }) {
  return (
    <ul className="space-y-3">
      {alertas.map((a) => {
        const Icono = a.clase === "tiempo" ? Clock : Thermometer;
        return (
          <li
            key={a.id}
            className="flex gap-3 rounded-xl border border-terracota/30 border-l-4 border-l-terracota bg-card p-4 shadow-xs"
          >
            <span className="flex size-9 shrink-0 items-center justify-center rounded-full bg-terracota-suave text-terracota">
              <Icono aria-hidden className="size-[18px]" />
            </span>
            <div className="min-w-0 flex-1">
              {conLote && (
                <Link
                  href={`/lotes/${a.lote_id}`}
                  className="text-sm font-semibold text-primary underline-offset-4 hover:underline"
                >
                  {a.lote_nombre} <span className="font-mono text-xs text-terracota">{a.lote_codigo}</span>
                </Link>
              )}
              <p className="mt-0.5 font-medium">{a.mensaje}</p>
              <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="first-letter:uppercase">Desde el {a.desde_texto}</span>
                {a.estado === "enviada" ? (
                  <Badge variant="outline" className="text-muted-foreground">
                    <Send aria-hidden />
                    Avisada por WhatsApp
                  </Badge>
                ) : (
                  <Badge variant="outline" className="border-terracota/40 text-terracota">
                    <TriangleAlert aria-hidden />
                    Sin avisar al caficultor
                  </Badge>
                )}
              </div>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
