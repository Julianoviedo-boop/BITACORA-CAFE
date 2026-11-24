import { SearchX } from "lucide-react";

import { Vacio } from "@/components/bitacora/estados";

// Sin enlace al panel: quien llega aquí es un comprador, no tiene clave.
export default function LoteNoEncontrado() {
  return (
    <main className="mx-auto max-w-xl px-4 py-16">
      <Vacio titulo="No encontramos este lote" icono={SearchX}>
        Revise que el enlace esté completo. Si lo recibió de un caficultor o de una asociación,
        pídales que se lo vuelvan a enviar.
      </Vacio>
    </main>
  );
}
