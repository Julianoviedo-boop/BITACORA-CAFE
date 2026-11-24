// Página pública del lote: la abre un comprador con el enlace o el QR, sin clave.
// Solo trae lo que la API marca como público (D-022): eventos confirmados, sin el
// texto original del caficultor, sin personas, precios ni horas.

import { ChartLine, Coffee, History, MapPin, Mountain, ShieldCheck } from "lucide-react";
import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { ErrorCarga, Vacio } from "@/components/bitacora/estados";
import { GraficaMediciones } from "@/components/bitacora/grafica-mediciones";
import { LineaTiempo } from "@/components/bitacora/linea-tiempo";
import { historiaPublica } from "@/lib/api";

export const dynamic = "force-dynamic";

const cargar = cache(historiaPublica);

type Props = { params: Promise<{ token: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const r = await cargar((await params).token);
  return {
    title: r.ok ? `Lote ${r.datos.lote.nombre}` : "Historia del lote",
    // El enlace es privado de quien lo recibe: que no salga en buscadores.
    robots: { index: false, follow: false },
  };
}

const numero = new Intl.NumberFormat("es-CO");

function Ficha({ etiqueta, valor }: { etiqueta: string; valor: string | null }) {
  if (!valor) return null;
  return (
    <div className="rounded-lg bg-primary-foreground/10 px-3 py-2">
      <dt className="text-xs text-primary-foreground/75">{etiqueta}</dt>
      <dd className="font-medium capitalize">{valor}</dd>
    </div>
  );
}

export default async function LotePublico({ params }: Props) {
  const { token } = await params;
  const resultado = await cargar(token);

  if (!resultado.ok && resultado.motivo === "no_encontrado") notFound();

  if (!resultado.ok) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-10">
        <ErrorCarga motivo="sin_conexion" />
      </main>
    );
  }

  const { lote, eventos, mediciones } = resultado.datos;
  const lugar = [lote.municipio, lote.departamento].filter(Boolean).join(", ");
  const desde = eventos[0]?.fecha_texto;
  const hasta = eventos[eventos.length - 1]?.fecha_texto;

  return (
    <div className="min-h-screen">
      <header className="bg-primary text-primary-foreground">
        <div className="mx-auto max-w-3xl px-4 pt-6 pb-8 sm:px-6 sm:pt-8">
          <p className="flex items-center gap-2 text-sm text-primary-foreground/80">
            <Coffee aria-hidden className="size-4 text-terracota-suave" />
            Historia de trazabilidad
            {lote.organizacion && <> · {lote.organizacion}</>}
          </p>
          <h1 className="mt-3 text-3xl font-semibold sm:text-4xl">{lote.nombre}</h1>
          <p className="mt-1 font-mono text-sm text-terracota-suave">Lote {lote.codigo}</p>
          {(lote.finca || lugar) && (
            <p className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-primary-foreground/90">
              {lote.finca && (
                <span className="inline-flex items-center gap-1">
                  <MapPin aria-hidden className="size-4" />
                  Finca {lote.finca}
                  {lugar && ` · ${lugar}`}
                </span>
              )}
              {lote.altitud_m && (
                <span className="inline-flex items-center gap-1">
                  <Mountain aria-hidden className="size-4" />
                  {numero.format(lote.altitud_m)} m s. n. m.
                </span>
              )}
            </p>
          )}
          <dl className="mt-5 grid grid-cols-2 gap-2 text-sm sm:max-w-sm">
            <Ficha etiqueta="Variedad" valor={lote.variedad} />
            <Ficha etiqueta="Proceso" valor={lote.proceso} />
          </dl>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
        <p className="flex gap-2 rounded-xl border bg-card p-4 text-sm">
          <ShieldCheck aria-hidden className="mt-0.5 size-5 shrink-0 text-primary" />
          <span>
            Cada registro lo contó el caficultor por WhatsApp el día del trabajo y lo confirmó antes de
            guardarlo.
            {desde && hasta && desde !== hasta && (
              <>
                {" "}
                Esta historia va del <span className="font-medium">{desde}</span> al{" "}
                <span className="font-medium">{hasta}</span>.
              </>
            )}
          </span>
        </p>

        {mediciones.length > 0 && (
          <section className="mt-10">
            <h2 className="flex items-center gap-2 text-xl font-semibold">
              <ChartLine aria-hidden className="size-5 text-terracota" />
              Mediciones
            </h2>
            <div className="mt-4">
              <GraficaMediciones series={mediciones} />
            </div>
          </section>
        )}

        <section className="mt-10">
          <h2 className="flex items-center gap-2 text-xl font-semibold">
            <History aria-hidden className="size-5 text-terracota" />
            Lo que se ha hecho en el lote
          </h2>
          <div className="mt-4">
            {eventos.length === 0 ? (
              <Vacio titulo="Todavía no hay registros">
                Este lote aún no tiene trabajos registrados. Vuelva a abrir el enlace más adelante.
              </Vacio>
            ) : (
              <LineaTiempo items={eventos.map((e, i) => ({ ...e, clave: `${e.fecha}-${i}` }))} />
            )}
          </div>
        </section>

        <footer className="mt-12 border-t pt-4 text-center text-xs text-muted-foreground">
          Bitácora de Café · Trazabilidad desde la finca
        </footer>
      </main>
    </div>
  );
}
