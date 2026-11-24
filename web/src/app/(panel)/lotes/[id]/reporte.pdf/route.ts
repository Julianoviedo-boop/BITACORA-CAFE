// Descarga del reporte en PDF desde el panel. El navegador pide aquí (detrás de la
// clave del panel) y el servidor de Next.js le pide el PDF a la api con el token,
// que nunca llega al navegador (D-020, D-023).

export const dynamic = "force-dynamic";

const FECHA = /^\d{4}-\d{2}-\d{2}$/;

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const entrada = new URL(request.url).searchParams;
  const consulta = new URLSearchParams();
  for (const clave of ["desde", "hasta"]) {
    const valor = entrada.get(clave);
    if (valor && FECHA.test(valor)) consulta.set(clave, valor);
  }

  const base = process.env.API_INTERNAL_URL ?? "http://localhost:8000";
  const ruta = `/lotes/${encodeURIComponent(id)}/reporte.pdf${consulta.size ? `?${consulta}` : ""}`;
  let resp: Response;
  try {
    resp = await fetch(`${base}${ruta}`, {
      cache: "no-store",
      headers: { Authorization: `Bearer ${process.env.PANEL_API_TOKEN ?? ""}` },
    });
  } catch {
    return texto("No pudimos conectarnos con el servidor. Intente de nuevo en un momento.", 502);
  }
  if (resp.status === 404 || resp.status === 422) return texto("No encontramos ese lote.", 404);
  if (!resp.ok) return texto("No se pudo generar el reporte. Intente de nuevo en un momento.", 502);

  return new Response(resp.body, {
    headers: {
      "Content-Type": "application/pdf",
      "Content-Disposition": resp.headers.get("content-disposition") ?? 'inline; filename="reporte.pdf"',
      "Cache-Control": "no-store",
    },
  });
}

function texto(mensaje: string, status: number) {
  return new Response(mensaje, { status, headers: { "Content-Type": "text/plain; charset=utf-8" } });
}
