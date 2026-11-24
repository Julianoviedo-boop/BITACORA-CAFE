import { BellOff } from "lucide-react";
import type { Metadata } from "next";

import { ErrorCarga, Vacio } from "@/components/bitacora/estados";
import { ListaAlertas } from "@/components/bitacora/lista-alertas";
import { listarAlertas } from "@/lib/api";

export const dynamic = "force-dynamic";
export const metadata: Metadata = { title: "Alertas" };

export default async function Alertas() {
  const resultado = await listarAlertas();

  return (
    <main className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <h1 className="text-3xl font-semibold">Alertas abiertas</h1>
      <p className="mt-1 text-muted-foreground">
        Lotes que necesitan atención: una medición fuera de rango o un trabajo que no se ha
        registrado a tiempo. Se cierran solas cuando llega el registro que faltaba.
      </p>

      {!resultado.ok ? (
        <ErrorCarga motivo={resultado.motivo} className="mt-6" />
      ) : resultado.datos.length === 0 ? (
        <Vacio titulo="No hay alertas abiertas" icono={BellOff} className="mt-6">
          Todos los lotes van bien. Si algo se sale de lo normal, aparece aquí.
        </Vacio>
      ) : (
        <>
          <p className="mt-6 text-sm font-medium">
            {resultado.datos.length} {resultado.datos.length === 1 ? "alerta" : "alertas"}, la más
            reciente primero
          </p>
          <div className="mt-3">
            <ListaAlertas alertas={resultado.datos} />
          </div>
        </>
      )}
    </main>
  );
}
