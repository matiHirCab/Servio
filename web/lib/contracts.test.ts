import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { AlertType, ObservationSchema, type Observation } from "./contracts";

const fixturesDir = join(__dirname, "..", "..", "fixtures");
const START = Date.parse("2026-10-17T20:00:00Z");

// Formato de fixtures/expected/*.alerts.json (no es parte del contrato de la API).
const ExpectedAlertSchema = z
  .object({
    at_seconds: z.number().int().nonnegative(),
    type: AlertType,
    table_id: z.string().min(1),
    expired_at_seconds: z.number().int().optional(),
  })
  .refine((a) => a.expired_at_seconds === undefined || a.expired_at_seconds >= a.at_seconds, {
    message: "expired_at_seconds tiene que ser >= at_seconds",
  });

function observation(ts = "2026-10-17T20:00:00Z", item: Record<string, unknown> = {}) {
  const base = { track_id: "T1-p1", kind: "plate", state: "lleno", confidence: 0.9 };
  return { schema_version: 1, camera_id: "cam-1", ts, tables: [{ table_id: "T1", items: [{ ...base, ...item }] }] };
}

function loadObservations(name: string): Observation[] {
  return readFileSync(join(fixturesDir, "observations", name), "utf8")
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => ObservationSchema.parse(JSON.parse(line)));
}

function itemsOf(obs: Observation, tableId: string) {
  return obs.tables.find((t) => t.table_id === tableId)?.items ?? [];
}

describe("contrato", () => {
  it("acepta null en los opcionales y ts con offset (lo que serializa Pydantic)", () => {
    const data = observation("2026-10-17T17:00:00-03:00", { fill_ratio: null, position: null });
    expect(ObservationSchema.safeParse({ ...data, tables: [{ ...data.tables[0], people_present: null }] }).success).toBe(true);
  });

  it.each([
    ["schema_version desconocida", { ...observation(), schema_version: 2 }],
    ["kind fuera de alcance", observation(undefined, { kind: "drink" })],
    ["ts sin zona", observation("2026-10-17T20:00:00")],
    ["confidence como string", observation(undefined, { confidence: "0.8" })],
    ["track_id repetido en una mesa", (() => {
      const data = observation();
      return { ...data, tables: [{ ...data.tables[0], items: [...data.tables[0].items, ...data.tables[0].items] }] };
    })()],
    ["table_id repetido", (() => {
      const data = observation();
      return { ...data, tables: [...data.tables, ...data.tables] };
    })()],
  ])("rechaza %s", (_, data) => {
    expect(ObservationSchema.safeParse(data).success).toBe(false);
  });
});

describe("fixtures/observations", () => {
  const files = readdirSync(join(fixturesDir, "observations")).filter((f) => f.endsWith(".jsonl"));

  it.each(files)("%s cumple el contrato, con una lectura cada 10 s desde 20:00:00", (file) => {
    const observations = loadObservations(file);
    expect(observations.length).toBeGreaterThan(0);
    observations.forEach((obs, i) => expect(Date.parse(obs.ts)).toBe(START + i * 10_000));
  });

  it("mesa_completa: T1-p1 pasa de lleno a vacío, en orden", () => {
    const states = loadObservations("mesa_completa.jsonl").flatMap((o) =>
      itemsOf(o, "T1").filter((it) => it.track_id === "T1-p1").map((it) => it.state),
    );
    const transitions = states.filter((s, i) => i === 0 || s !== states[i - 1]);
    expect(transitions).toEqual(["lleno", "comiendo", "por_terminar", "vacio"]);
  });

  it("plato_lleno_mucho: T2-p1 sigue lleno con gente al menos 12 minutos", () => {
    const withPeople = loadObservations("plato_lleno_mucho.jsonl").filter(
      (obs) =>
        obs.tables.find((t) => t.table_id === "T2")?.people_present !== false &&
        itemsOf(obs, "T2").some((it) => it.track_id === "T2-p1" && it.state === "lleno"),
    );
    const minutes = (Date.parse(withPeople[withPeople.length - 1].ts) - Date.parse(withPeople[0].ts)) / 60_000;
    expect(minutes).toBeGreaterThanOrEqual(12);
  });

  it("id_cambiado: un id cambia manteniendo kind y position", () => {
    const observations = loadObservations("id_cambiado.jsonl");
    let swaps = 0;
    for (let i = 1; i < observations.length; i++) {
      const prev = itemsOf(observations[i - 1], "T1");
      const curr = itemsOf(observations[i], "T1");
      const gone = prev.filter((p) => !curr.some((c) => c.track_id === p.track_id));
      const added = curr.filter((c) => !prev.some((p) => p.track_id === c.track_id));
      for (const g of gone) {
        if (added.some((a) => a.kind === g.kind && a.position === g.position)) swaps++;
      }
    }
    expect(swaps).toBe(1);
  });
});

describe("fixtures/expected", () => {
  it.each(["mesa_completa", "plato_lleno_mucho", "id_cambiado"])("%s.alerts.json tiene el formato esperado", (name) => {
    const raw: unknown = JSON.parse(readFileSync(join(fixturesDir, "expected", `${name}.alerts.json`), "utf8"));
    expect(() => ExpectedAlertSchema.array().parse(raw)).not.toThrow();
  });
});
