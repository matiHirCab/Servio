import { describe, expect, it, vi } from "vitest";
import type { Alert } from "../contracts";
import { ConsoleNotifier } from "./types";

const alert: Alert = {
  id: "a_1",
  table_id: "T1",
  type: "plato_vacio",
  message: "Mesa 1: hay platos vacíos para retirar.",
  created_at: "2026-10-17T20:04:00Z",
  status: "abierta",
  acked_by: null,
  acked_at: null,
  resolved_at: null,
};

describe("ConsoleNotifier", () => {
  it("devuelve un mensaje distinto por envío", async () => {
    vi.spyOn(console, "log").mockImplementation(() => {});
    const notifier = new ConsoleNotifier();
    const waiter = { id: "w1", name: "Ana", telegram_chat_id: "123" };
    const first = await notifier.send(alert, waiter);
    const second = await notifier.send(alert, waiter);
    expect(first).toMatchObject({ channel: "telegram", chat_id: "123" });
    expect(second.message_id).not.toBe(first.message_id);
    await expect(notifier.update({ ...alert, status: "resuelta" }, first)).resolves.toBeUndefined();
  });
});
