import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { z } from "zod";
import { AlertType, ObservationSchema, type Observation } from "./contracts";

const fixturesDir = join(__dirname, "..", "..", "fixtures");
const START = Date.parse("2026-10-17T20:00:00Z");

// Formato de fixtures/expected/*.alerts.json (no es parte del contrato de la API).
const ExpectedAlertSchema = z.object({
  at_seconds: z.number().int().nonnegative(),
  type: AlertType,
  table_id: z.string().min(1),
});

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
  it("rechaza un kind fuera de alcance", () => {
    const bad = {
      schema_version: 1,
      camera_id: "cam-1",
      ts: "2026-10-17T20:00:00.000Z",
      tables: [{ table_id: "T1", items: [{ track_id: "T1-d1", kind: "drink", state: "vacio", confidence: 0.9 }] }],
    };
    expect(ObservationSchema.safeParse(bad).success).toBe(false);
  });

  it("rechaza schema_version desconocida", () => {
    const bad = { schema_version: 2, camera_id: "cam-1", ts: "2026-10-17T20:00:00.000Z", tables: [] };
    expect(ObservationSchema.safeParse(bad).success).toBe(false);
  });
});

describe("fixtures/observations", () => {
  const files = readdirSync(join(fixturesDir, "observations")).filter((f) => f.endsWith(".jsonl"));

  it.each(files)("%s cumple el contrato, con una lectura cada 10 s desde 20:00:00", (file) => {
    const observations = loadObservations(file);
    expect(observations.length).toBeGreaterThan(0);
    observations.forEach((obs, i) => expect(Date.parse(obs.ts)).toBe(START + i * 10_000));
  });

  it("mesa_completa: T1 pasa de lleno a vacío", () => {
    const states = new Set(loadObservations("mesa_completa.jsonl").flatMap((o) => itemsOf(o, "T1").map((it) => it.state)));
    expect(states).toEqual(new Set(["lleno", "comiendo", "por_terminar", "vacio"]));
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
  it("mesa_completa.alerts.json tiene el formato esperado", () => {
    const raw: unknown = JSON.parse(readFileSync(join(fixturesDir, "expected", "mesa_completa.alerts.json"), "utf8"));
    expect(ExpectedAlertSchema.array().min(1).parse(raw)).toBeTruthy();
  });
});
