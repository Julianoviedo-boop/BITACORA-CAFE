// Consultas a la API desde el servidor de Next.js. Nunca se llama desde el navegador:
// el token (PANEL_API_TOKEN) no debe llegar al cliente. Ver docs/decisiones.md, D-020.

export type Lote = {
  id: string;
  codigo: string;
  nombre: string;
  finca: string | null;
  variedad: string | null;
  proceso: string | null;
  activo: boolean;
  eventos: number;
  ultimo_evento: string | null;
  ultimo_evento_texto: string | null;
};

export type Dato = { etiqueta: string; valor: string };

export type Evento = {
  id: string;
  tipo: string;
  actividad: string;
  fecha: string;
  fecha_texto: string;
  hora: string | null;
  datos: Dato[];
  texto_original: string | null;
};

export type HistoriaLote = { lote: Lote; eventos: Evento[] };

export type Resultado<T> =
  | { ok: true; datos: T }
  | { ok: false; motivo: "no_encontrado" | "sin_configurar" | "sin_conexion" };

async function consultar<T>(ruta: string): Promise<Resultado<T>> {
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  const token = process.env.PANEL_API_TOKEN ?? "";
  try {
    const resp = await fetch(`${base}${ruta}`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${token}` },
    });
    if (resp.status === 404 || resp.status === 422) return { ok: false, motivo: "no_encontrado" };
    if (resp.status === 401 || resp.status === 503) return { ok: false, motivo: "sin_configurar" };
    if (!resp.ok) return { ok: false, motivo: "sin_conexion" };
    return { ok: true, datos: (await resp.json()) as T };
  } catch {
    return { ok: false, motivo: "sin_conexion" };
  }
}

export async function listarLotes(): Promise<Resultado<Lote[]>> {
  const r = await consultar<{ lotes: Lote[] }>("/panel/lotes");
  return r.ok ? { ok: true, datos: r.datos.lotes } : r;
}

export function historiaLote(id: string): Promise<Resultado<HistoriaLote>> {
  return consultar<HistoriaLote>(`/panel/lotes/${encodeURIComponent(id)}`);
}

export const MENSAJES_ERROR: Record<"no_encontrado" | "sin_configurar" | "sin_conexion", string> = {
  no_encontrado: "No encontramos ese lote.",
  sin_configurar:
    "El panel no está bien configurado para hablar con el servidor. Avísele a quien administra el sistema.",
  sin_conexion: "No pudimos conectarnos con el servidor. Intente de nuevo en un momento.",
};
