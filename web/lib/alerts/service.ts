// Servicio de alertas (11-tareas.md, T0-2 → P2-8). Lo usan las rutas /api/alerts/:id/{ack,resolve}
// y el webhook de Telegram. Al cambiar de estado, llama a Notifier.update por cada fila de alert_messages.
import type { Alert } from "../contracts";
import type { Waiter } from "../notify/types";

export async function ackAlert(alertId: string, waiterId: string): Promise<Alert> {
  throw new Error("not implemented");
}

export async function resolveAlert(alertId: string, waiterId: string): Promise<Alert> {
  throw new Error("not implemented");
}

export async function registerWaiter(chatId: string, name: string): Promise<Waiter> {
  throw new Error("not implemented");
}

export async function assignTables(waiterId: string, tableIds: string[]): Promise<void> {
  throw new Error("not implemented");
}
