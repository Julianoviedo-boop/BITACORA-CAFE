// Línea de tiempo vertical de los eventos de un lote. La usan el panel (con estado
// y texto original) y la página pública (solo confirmados, sin texto original).

import { CircleCheck, CircleX, Hourglass, Quote } from "lucide-react";

import { IconoEvento } from "@/components/bitacora/icono-evento";
import { Badge } from "@/components/ui/badge";
import type { Dato, EstadoEvento } from "@/lib/api";
import { cn } from "@/lib/utils";

export type ItemLinea = {
  clave: string;
  tipo: string;
  actividad: string;
  fecha: string;
  fecha_texto: string;
  hora?: string | null;
  datos: Dato[];
  texto_original?: string | null;
  estado?: EstadoEvento;
};

const ESTADOS: Record<EstadoEvento, { texto: string; icono: typeof CircleCheck; clase: string }> = {
  confirmado: { texto: "Confirmado", icono: CircleCheck, clase: "bg-verde-suave text-primary" },
  pendiente: {
    texto: "Esperando confirmación",
    icono: Hourglass,
    clase: "bg-terracota-suave text-terracota",
  },
  rechazado: { texto: "Cancelado", icono: CircleX, clase: "bg-muted text-muted-foreground" },
};

function EstadoBadge({ estado }: { estado: EstadoEvento }) {
  const { texto, icono: Icono, clase } = ESTADOS[estado];
  return (
    <Badge variant="secondary" className={clase}>
      <Icono aria-hidden />
      {texto}
    </Badge>
  );
}

export function LineaTiempo({ items }: { items: ItemLinea[] }) {
  return (
    <ol className="relative">
      {items.map((item, i) => {
        const cancelado = item.estado === "rechazado";
        const pendiente = item.estado === "pendiente";
        return (
          <li key={item.clave} className="relative flex gap-3 pb-6 last:pb-0 sm:gap-4">
            {/* Línea vertical que une los íconos */}
            {i < items.length - 1 && (
              <span aria-hidden className="absolute top-10 bottom-0 left-[19px] w-0.5 bg-border" />
            )}
            <span
              className={cn(
                "relative z-[1] flex size-10 shrink-0 items-center justify-center rounded-full border-2 bg-card",
                cancelado
                  ? "border-border text-muted-foreground"
                  : pendiente
                    ? "border-dashed border-terracota text-terracota"
                    : "border-primary/30 text-primary",
              )}
            >
              <IconoEvento tipo={item.tipo} className="size-[18px]" />
            </span>

            <article
              className={cn(
                "min-w-0 flex-1 rounded-xl border bg-card p-4 shadow-xs",
                pendiente && "border-dashed border-terracota/50",
                cancelado && "opacity-70",
              )}
            >
              <div className="flex flex-wrap items-start justify-between gap-x-3 gap-y-1">
                <div>
                  <h3 className={cn("text-lg font-semibold", cancelado && "line-through decoration-1")}>
                    {item.actividad}
                  </h3>
                  <p className="text-sm text-muted-foreground first-letter:uppercase">
                    <time dateTime={item.fecha}>{item.fecha_texto}</time>
                    {item.hora && <> · {item.hora}</>}
                  </p>
                </div>
                {item.estado && <EstadoBadge estado={item.estado} />}
              </div>

              {item.datos.length > 0 && (
                <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
                  {item.datos.map((d) => (
                    <div key={d.etiqueta} className="contents">
                      <dt className="text-muted-foreground">{d.etiqueta}</dt>
                      <dd className="font-medium break-words">{d.valor}</dd>
                    </div>
                  ))}
                </dl>
              )}

              {item.texto_original && (
                <blockquote className="mt-3 flex gap-2 rounded-lg bg-muted/70 px-3 py-2 text-sm">
                  <Quote aria-hidden className="mt-0.5 size-4 shrink-0 text-terracota" />
                  <div>
                    <p className="text-xs font-medium text-muted-foreground">Lo que contó el caficultor</p>
                    <p className="mt-0.5 whitespace-pre-line italic">{item.texto_original}</p>
                  </div>
                </blockquote>
              )}
            </article>
          </li>
        );
      })}
    </ol>
  );
}
