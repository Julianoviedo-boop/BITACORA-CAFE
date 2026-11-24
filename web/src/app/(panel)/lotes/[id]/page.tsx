import { ArrowLeft, ChartLine, FileDown, History, MapPin, TriangleAlert } from "lucide-react";
import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";

import { ErrorCarga, Vacio } from "@/components/bitacora/estados";
import { GraficaMediciones } from "@/components/bitacora/grafica-mediciones";
import { LineaTiempo } from "@/components/bitacora/linea-tiempo";
import { ListaAlertas } from "@/components/bitacora/lista-alertas";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { historiaLote } from "@/lib/api";

export const dynamic = "force-dynamic";

// Una sola consulta por visita, aunque la usen el título y la página.
const cargarLote = cache(historiaLote);

type Props = { params: Promise<{ id: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const r = await cargarLote((await params).id);
  return { title: r.ok ? `${r.datos.lote.nombre} (${r.datos.lote.codigo})` : "Lote" };
}

function Seccion({
  titulo,
  icono: Icono,
  children,
  descripcion,
}: {
  titulo: string;
  icono: typeof History;
  children: React.ReactNode;
  descripcion?: string;
}) {
  return (
    <section className="mt-10">
      <h2 className="flex items-center gap-2 text-xl font-semibold">
        <Icono aria-hidden className="size-5 text-terracota" />
        {titulo}
      </h2>
      {descripcion && <p className="mt-1 text-sm text-muted-foreground">{descripcion}</p>}
      <div className="mt-4">{children}</div>
    </section>
  );
}

export default async function DetalleLote({ params }: Props) {
  const { id } = await params;
  const resultado = await cargarLote(id);

  if (!resultado.ok && resultado.motivo === "no_encontrado") notFound();

  return (
    <main className="mx-auto max-w-5xl px-4 py-6 sm:px-6 sm:py-8">
      <Link
        href="/lotes"
        className="inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
      >
        <ArrowLeft aria-hidden className="size-4" />
        Volver a los lotes
      </Link>

      {!resultado.ok ? (
        <ErrorCarga motivo={resultado.motivo} className="mt-6" />
      ) : (
        (() => {
          const { lote, eventos, mediciones, alertas } = resultado.datos;
          const pendientes = eventos.filter((e) => e.estado === "pendiente").length;
          return (
            <>
              <header className="mt-4">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p className="font-mono text-sm font-medium tracking-wide text-terracota">{lote.codigo}</p>
                    <h1 className="text-3xl font-semibold sm:text-4xl">{lote.nombre}</h1>
                  </div>
                  {/* Un enlace normal: el PDF lo arma el servidor; no hace falta JavaScript. */}
                  <Button asChild variant="outline" className="bg-card">
                    <a href={`/lotes/${lote.id}/reporte.pdf`} target="_blank" rel="noopener">
                      <FileDown aria-hidden />
                      Reporte en PDF
                    </a>
                  </Button>
                </div>
                <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
                  {lote.finca && (
                    <span className="inline-flex items-center gap-1">
                      <MapPin aria-hidden className="size-4" />
                      Finca {lote.finca}
                    </span>
                  )}
                  {lote.variedad && <span>Variedad {lote.variedad}</span>}
                  {lote.proceso && <span>Proceso {lote.proceso}</span>}
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  {lote.etapa ? (
                    <Badge className="bg-verde-suave text-primary">En {lote.etapa.toLowerCase()}</Badge>
                  ) : (
                    <Badge variant="outline" className="text-muted-foreground">
                      Sin proceso en curso
                    </Badge>
                  )}
                  <Badge variant="outline">
                    {lote.eventos} {lote.eventos === 1 ? "registro confirmado" : "registros confirmados"}
                  </Badge>
                  {pendientes > 0 && (
                    <Badge variant="outline" className="border-terracota/40 text-terracota">
                      {pendientes} esperando confirmación
                    </Badge>
                  )}
                  {!lote.activo && <Badge variant="outline">Inactivo</Badge>}
                </div>
              </header>

              {alertas.length > 0 && (
                <Seccion titulo="Alertas abiertas" icono={TriangleAlert}>
                  <ListaAlertas alertas={alertas} conLote={false} />
                </Seccion>
              )}

              <Seccion
                titulo="Mediciones"
                icono={ChartLine}
                descripcion="Temperatura, humedad y otros datos medidos, según los registros confirmados."
              >
                {mediciones.length === 0 ? (
                  <Vacio titulo="Todavía no hay mediciones" icono={ChartLine}>
                    Aparecen cuando el caficultor cuenta, por ejemplo, a cuántos grados está la
                    fermentación o cuánta humedad marcó el café.
                  </Vacio>
                ) : (
                  <GraficaMediciones series={mediciones} />
                )}
              </Seccion>

              <Seccion
                titulo="Historia del lote"
                icono={History}
                descripcion={
                  eventos.length > 0
                    ? "De lo más antiguo a lo más reciente. Solo lo confirmado por el caficultor cuenta en la historia."
                    : undefined
                }
              >
                {eventos.length === 0 ? (
                  <Vacio titulo="Todavía no hay registros">
                    Aparecen aquí cuando el caficultor cuenta por WhatsApp lo que hizo en este lote.
                  </Vacio>
                ) : (
                  <LineaTiempo items={eventos.map((e) => ({ ...e, clave: e.id }))} />
                )}
              </Seccion>
            </>
          );
        })()
      )}
    </main>
  );
}
