// Consultas a la API desde el servidor de Next.js. Nunca se llama desde el navegador:
// el token (PANEL_API_TOKEN) no debe llegar al cliente. Ver docs/decisiones.md, D-020 y D-022.

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
  ultima_actividad: string | null;
  etapa: string | null;
  alertas_abiertas: number;
};

export type Dato = { etiqueta: string; valor: string };

export type EstadoEvento = "pendiente" | "confirmado" | "rechazado";

export type Evento = {
  id: string;
  tipo: string;
  actividad: string;
  fecha: string;
  fecha_texto: string;
  hora: string | null;
  datos: Dato[];
  texto_original: string | null;
  estado: EstadoEvento;
};

export type PuntoMedicion = { momento: string; fecha_texto: string; valor: number };

export type SerieMedicion = {
  campo: string;
  etiqueta: string;
  unidad: string | null;
  puntos: PuntoMedicion[];
};

export type Alerta = {
  id: string;
  lote_id: string;
  lote_codigo: string;
  lote_nombre: string;
  mensaje: string;
  clase: "valor" | "tiempo";
  estado: "pendiente" | "enviada";
  desde: string;
  desde_texto: string;
};

export type HistoriaLote = {
  lote: Lote;
  eventos: Evento[];
  mediciones: SerieMedicion[];
  alertas: Alerta[];
};

export type LotePublico = {
  codigo: string;
  nombre: string;
  organizacion: string | null;
  finca: string | null;
  municipio: string | null;
  departamento: string | null;
  altitud_m: number | null;
  variedad: string | null;
  proceso: string | null;
};

export type EventoPublico = {
  actividad: string;
  tipo: string;
  fecha: string;
  fecha_texto: string;
  datos: Dato[];
};

export type HistoriaPublica = {
  lote: LotePublico;
  eventos: EventoPublico[];
  mediciones: SerieMedicion[];
};

export type Motivo = "no_encontrado" | "sin_configurar" | "sin_conexion";

export type Resultado<T> = { ok: true; datos: T } | { ok: false; motivo: Motivo };

async function consultar<T>(ruta: string, conToken = true): Promise<Resultado<T>> {
  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  const headers: Record<string, string> = {};
  if (conToken) headers.Authorization = `Bearer ${process.env.PANEL_API_TOKEN ?? ""}`;
  try {
    const resp = await fetch(`${base}${ruta}`, { cache: "no-store", headers });
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

export async function listarAlertas(): Promise<Resultado<Alerta[]>> {
  const r = await consultar<{ alertas: Alerta[] }>("/panel/alertas");
  return r.ok ? { ok: true, datos: r.datos.alertas } : r;
}

// Página pública: sin el token del panel. La llave es el token del lote.
export function historiaPublica(token: string): Promise<Resultado<HistoriaPublica>> {
  return consultar<HistoriaPublica>(`/publico/lotes/${encodeURIComponent(token)}`, false);
}

export const MENSAJES_ERROR: Record<Motivo, string> = {
  no_encontrado: "No encontramos lo que busca.",
  sin_configurar:
    "El panel no está bien configurado para hablar con el servidor. Avísele a quien administra el sistema.",
  sin_conexion: "No pudimos conectarnos con el servidor. Intente de nuevo en un momento.",
};
