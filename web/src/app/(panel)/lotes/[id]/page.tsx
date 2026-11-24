import Link from "next/link";
import { notFound } from "next/navigation";

import { historiaLote, MENSAJES_ERROR, type Evento } from "@/lib/api";

export const dynamic = "force-dynamic";

function TarjetaEvento({ evento }: { evento: Evento }) {
  return (
    <li className="relative pl-5">
      <span className="absolute -left-[7px] top-5 h-3 w-3 rounded-full bg-amber-700" aria-hidden />
      <article className="rounded-lg border border-stone-200 bg-white p-4">
        <p className="text-sm text-stone-600">
          <time dateTime={evento.fecha}>{evento.fecha_texto}</time>
          {evento.hora && <> · {evento.hora}</>}
        </p>
        <h3 className="mt-1 text-lg font-semibold">{evento.actividad}</h3>
        {evento.datos.length > 0 && (
          <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {evento.datos.map((d) => (
              <div key={d.etiqueta} className="contents">
                <dt className="text-stone-600">{d.etiqueta}</dt>
                <dd className="font-medium">{d.valor}</dd>
              </div>
            ))}
          </dl>
        )}
        {evento.texto_original && (
          <details className="mt-3 text-sm">
            <summary className="cursor-pointer text-stone-600">Lo que contó el caficultor</summary>
            <p className="mt-1 whitespace-pre-line text-stone-800">{evento.texto_original}</p>
          </details>
        )}
      </article>
    </li>
  );
}

export default async function HistoriaDelLote({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const resultado = await historiaLote(id);

  if (!resultado.ok && resultado.motivo === "no_encontrado") notFound();

  return (
    <main className="mx-auto max-w-3xl p-4 sm:p-8">
      <Link href="/lotes" className="text-sm text-amber-800 hover:underline">
        ← Volver a los lotes
      </Link>

      {!resultado.ok ? (
        <p className="mt-6 rounded-lg border border-red-200 bg-red-50 p-4 text-red-800">
          {MENSAJES_ERROR[resultado.motivo]}
        </p>
      ) : (
        <>
          <header className="mt-4">
            <h1 className="text-3xl font-bold">
              {resultado.datos.lote.nombre}{" "}
              <span className="font-normal text-stone-500">· {resultado.datos.lote.codigo}</span>
            </h1>
            <p className="mt-2 text-stone-600">
              {[
                resultado.datos.lote.finca && `Finca ${resultado.datos.lote.finca}`,
                resultado.datos.lote.variedad && `Variedad ${resultado.datos.lote.variedad}`,
                resultado.datos.lote.proceso && `Proceso ${resultado.datos.lote.proceso}`,
                !resultado.datos.lote.activo && "Inactivo",
              ]
                .filter(Boolean)
                .join(" · ")}
            </p>
          </header>

          <h2 className="mt-8 text-xl font-semibold">Historia del lote</h2>
          {resultado.datos.eventos.length === 0 ? (
            <p className="mt-4 rounded-lg border border-stone-200 bg-white p-4 text-stone-700">
              Todavía no hay actividades. Aparecen aquí cuando el caficultor cuenta por WhatsApp lo
              que hizo y lo confirma.
            </p>
          ) : (
            <>
              <p className="mt-1 text-sm text-stone-600">
                De la más antigua a la más reciente. Solo se muestran las actividades que el
                caficultor confirmó.
              </p>
              <ol className="mt-4 space-y-4 border-l-2 border-amber-200 pl-0">
                {resultado.datos.eventos.map((evento) => (
                  <TarjetaEvento key={evento.id} evento={evento} />
                ))}
              </ol>
            </>
          )}
        </>
      )}
    </main>
  );
}
