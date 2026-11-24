import { ChevronRight, MapPin, TriangleAlert } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";

import { ErrorCarga, Vacio } from "@/components/bitacora/estados";
import { Badge } from "@/components/ui/badge";
import { listarLotes, type Lote } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Lotes" };

function TarjetaLote({ lote }: { lote: Lote }) {
  const detalles = [lote.variedad, lote.proceso].filter(Boolean).join(" · ");
  return (
    <li>
      <Link
        href={`/lotes/${lote.id}`}
        className="group flex h-full flex-col rounded-xl border bg-card p-4 shadow-xs transition-colors hover:border-primary/50 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="font-mono text-xs font-medium tracking-wide text-terracota">{lote.codigo}</p>
            <h2 className="mt-0.5 truncate text-xl font-semibold">{lote.nombre}</h2>
          </div>
          {lote.alertas_abiertas > 0 && (
            <Badge className="bg-terracota text-terracota-foreground">
              <TriangleAlert aria-hidden />
              {lote.alertas_abiertas} {lote.alertas_abiertas === 1 ? "alerta" : "alertas"}
            </Badge>
          )}
        </div>

        {(lote.finca || detalles) && (
          <p className="mt-1 flex flex-wrap items-center gap-x-2 text-sm text-muted-foreground">
            {lote.finca && (
              <span className="inline-flex items-center gap-1">
                <MapPin aria-hidden className="size-3.5" />
                {lote.finca}
              </span>
            )}
            {detalles && <span className="capitalize">{detalles}</span>}
          </p>
        )}

        <div className="mt-4 flex flex-wrap gap-2">
          {lote.etapa ? (
            <Badge variant="secondary" className="bg-verde-suave text-primary">
              En {lote.etapa.toLowerCase()}
            </Badge>
          ) : (
            <Badge variant="outline" className="text-muted-foreground">
              Sin proceso en curso
            </Badge>
          )}
          {!lote.activo && <Badge variant="outline">Inactivo</Badge>}
        </div>

        <div aria-hidden className="min-h-4 flex-1" />
        <div className="flex items-end justify-between gap-2 border-t pt-3 text-sm">
          {lote.ultima_actividad && lote.ultimo_evento_texto ? (
            <p>
              <span className="text-muted-foreground">Último registro: </span>
              <span className="font-medium">{lote.ultima_actividad}</span>
              <span className="block text-muted-foreground first-letter:uppercase">{lote.ultimo_evento_texto}</span>
            </p>
          ) : (
            <p className="text-muted-foreground">Todavía no tiene registros.</p>
          )}
          <ChevronRight
            aria-hidden
            className="size-5 shrink-0 text-muted-foreground transition-transform group-hover:translate-x-0.5"
          />
        </div>
      </Link>
    </li>
  );
}

export default async function Lotes() {
  const resultado = await listarLotes();
  const conAlertas = resultado.ok ? resultado.datos.filter((l) => l.alertas_abiertas > 0).length : 0;

  return (
    <main className="mx-auto max-w-5xl px-4 py-8 sm:px-6">
      <h1 className="text-3xl font-semibold">Lotes</h1>
      <p className="mt-1 text-muted-foreground">
        Toque un lote para ver todo lo que se ha hecho en él.
        {conAlertas > 0 && ` ${conAlertas} ${conAlertas === 1 ? "lote tiene" : "lotes tienen"} alertas.`}
      </p>

      {!resultado.ok ? (
        <ErrorCarga motivo={resultado.motivo} className="mt-6" />
      ) : resultado.datos.length === 0 ? (
        <Vacio titulo="Todavía no hay lotes" className="mt-6">
          Se crean solos cuando un caficultor nombra un lote nuevo por WhatsApp y confirma que lo
          quiere crear.
        </Vacio>
      ) : (
        <ul className="mt-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {resultado.datos.map((lote) => (
            <TarjetaLote key={lote.id} lote={lote} />
          ))}
        </ul>
      )}
    </main>
  );
}
