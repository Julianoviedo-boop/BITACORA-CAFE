"use client";

// Una gráfica pequeña por medición (temperatura, humedad...): cada una tiene su
// unidad, así que no se mezclan en el mismo eje. Qué datos se grafican lo dice
// `grafica: true` en config/eventos.yaml.

import { CartesianGrid, Line, LineChart, XAxis, YAxis } from "recharts";

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ChartContainer, ChartTooltip, ChartTooltipContent, type ChartConfig } from "@/components/ui/chart";
import type { SerieMedicion } from "@/lib/api";

const COLORES = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];

const diaCorto = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  day: "numeric",
  month: "short",
});
const diaYHora = new Intl.DateTimeFormat("es-CO", {
  timeZone: "America/Bogota",
  weekday: "short",
  day: "numeric",
  month: "short",
  hour: "numeric",
  minute: "2-digit",
});
const numero = new Intl.NumberFormat("es-CO", { maximumFractionDigits: 2 });

function conUnidad(valor: number, unidad: string | null) {
  // Igual que en el resumen de WhatsApp: "24,5 °C", "11 %", "36 horas".
  return unidad ? `${numero.format(valor)} ${unidad}` : numero.format(valor);
}

function Grafica({ serie, color }: { serie: SerieMedicion; color: string }) {
  const config = { valor: { label: serie.etiqueta, color } } satisfies ChartConfig;
  const datos = serie.puntos.map((p) => ({ t: new Date(p.momento).getTime(), valor: p.valor }));
  const valores = serie.puntos.map((p) => p.valor);
  // Una marca por día en el eje: dos mediciones el mismo día no repiten la fecha.
  const marcas = [...new Map(datos.map((d) => [diaCorto.format(d.t), d.t])).values()];
  const ultimo = valores[valores.length - 1];

  return (
    <Card className="gap-3 py-4">
      <CardHeader className="px-4">
        <CardTitle className="font-serif text-base">{serie.etiqueta}</CardTitle>
        <CardDescription>
          {serie.puntos.length === 1
            ? `Una medición: ${conUnidad(ultimo, serie.unidad)}`
            : `${serie.puntos.length} mediciones · la última: ${conUnidad(ultimo, serie.unidad)}`}
        </CardDescription>
      </CardHeader>
      <CardContent className="px-2 sm:px-4">
        <ChartContainer config={config} className="aspect-auto h-44 w-full">
          <LineChart data={datos} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} />
            <XAxis
              dataKey="t"
              type="number"
              scale="time"
              domain={["dataMin", "dataMax"]}
              ticks={marcas}
              tickFormatter={(t: number) => diaCorto.format(t)}
              tickLine={false}
              axisLine={false}
              tickMargin={8}
              minTickGap={24}
              padding={{ left: 12, right: 12 }}
            />
            <YAxis
              width={44}
              tickLine={false}
              axisLine={false}
              domain={["auto", "auto"]}
              tickFormatter={(v: number) => numero.format(v)}
            />
            <ChartTooltip
              content={
                <ChartTooltipContent
                  labelFormatter={(_, payload) => {
                    const t = payload?.[0]?.payload?.t;
                    return typeof t === "number" ? diaYHora.format(t) : "";
                  }}
                  formatter={(v) => (
                    <span className="font-medium tabular-nums">{conUnidad(Number(v), serie.unidad)}</span>
                  )}
                />
              }
            />
            <Line
              dataKey="valor"
              type="monotone"
              stroke="var(--color-valor)"
              strokeWidth={2}
              dot={{ r: 3, fill: "var(--color-valor)" }}
              activeDot={{ r: 5 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ChartContainer>
      </CardContent>
    </Card>
  );
}

export function GraficaMediciones({ series }: { series: SerieMedicion[] }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {series.map((serie, i) => (
        <Grafica key={`${serie.campo}-${serie.unidad}`} serie={serie} color={COLORES[i % COLORES.length]} />
      ))}
    </div>
  );
}
