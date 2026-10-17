// Contrato entre módulos. Fuente de verdad: docs/context/03-contratos.md.
// Si cambiás algo acá, cambiá también el doc, vision/servio_vision/contracts.py y fixtures/.
import { z } from "zod";

// Solo platos de comida: bebidas fuera de alcance.
export const ItemKind = z.enum(["plate"]);
export const PlateState = z.enum(["lleno", "comiendo", "por_terminar", "vacio"]);
export const AlertType = z.enum(["mesa_por_terminar", "plato_vacio", "plato_lleno_mucho"]);
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

export const TableReadingSchema = z.object({
  table_id: z.string().min(1),
  // Si falta (o es null) se asume true (ver 03-contratos.md).
  people_present: z.boolean().nullish(),
  items: z.array(ItemSchema),
});

export const ObservationSchema = z.object({
  schema_version: z.literal(1),
  camera_id: z.string().min(1),
  ts: isoDate,
  tables: z.array(TableReadingSchema),
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
