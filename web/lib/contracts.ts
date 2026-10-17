// Contrato entre módulos. Fuente de verdad: docs/context/03-contratos.md.
// Si cambiás algo acá, cambiá también el doc, vision/servio_vision/contracts.py y fixtures/.
import { z } from "zod";

// Solo platos de comida: bebidas fuera de alcance. `visible` no viaja al backend (solo vive en la visión).
export const ItemKind = z.enum(["plate"]);
export const PlateState = z.enum(["lleno", "comiendo", "por_terminar", "vacio"]);
export const AlertType = z.enum(["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho", "esperando_plato"]);
export const AlertStatus = z.enum(["abierta", "en_camino", "resuelta", "expirada"]);
export const TableStatus = z.enum(["libre", "servida", "comiendo", "por_terminar", "terminada"]);

const ratio = z.number().min(0).max(1);
// ISO 8601 con zona (Z o -03:00); sin zona se rechaza.
const isoDate = z.string().datetime({ offset: true });

export const ItemSchema = z.object({
  track_id: z.string().min(1),
  kind: ItemKind,
  state: PlateState,
  confidence: ratio,
  // nullish: Pydantic serializa los opcionales ausentes como null.
  fill_ratio: ratio.nullish(),
  position: z.string().nullish(),
});

// Rechaza ids repetidos: la reconciliación y "ausente en 2 lecturas" suponen que son únicos.
function uniqueBy<T>(key: (t: T) => string, label: string) {
  return (list: T[], ctx: z.RefinementCtx) => {
    const seen = new Set<string>();
    for (const item of list) {
      const k = key(item);
      if (seen.has(k)) ctx.addIssue({ code: z.ZodIssueCode.custom, message: `${label} repetido: ${k}` });
      seen.add(k);
    }
  };
}

export const TableReadingSchema = z.object({
  table_id: z.string().min(1),
  // Si falta (o es null) se asume true (ver 03-contratos.md).
  people_present: z.boolean().nullish(),
  items: z.array(ItemSchema).superRefine(uniqueBy((it) => it.track_id, "track_id")),
});

export const ObservationSchema = z.object({
  schema_version: z.literal(1),
  camera_id: z.string().min(1),
  ts: isoDate,
  tables: z.array(TableReadingSchema).superRefine(uniqueBy((t) => t.table_id, "table_id")),
});

export const AlertSchema = z.object({
  id: z.string().min(1),
  table_id: z.string().min(1),
  type: AlertType,
  message: z.string(),
  created_at: isoDate,
  status: AlertStatus,
  acked_by: z.string().nullable(),
  acked_at: isoDate.nullable(),
  resolved_at: isoDate.nullable(),
});

export type ItemKind = z.infer<typeof ItemKind>;
export type PlateState = z.infer<typeof PlateState>;
export type AlertType = z.infer<typeof AlertType>;
export type AlertStatus = z.infer<typeof AlertStatus>;
export type TableStatus = z.infer<typeof TableStatus>;
export type Item = z.infer<typeof ItemSchema>;
export type TableReading = z.infer<typeof TableReadingSchema>;
export type Observation = z.infer<typeof ObservationSchema>;
export type Alert = z.infer<typeof AlertSchema>;
