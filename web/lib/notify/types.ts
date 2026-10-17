// Interfaz Notifier (03-contratos.md, «Backend → Notificaciones»). P2 la llama; P1 implementa
// TelegramNotifier en telegram.ts. Mientras tanto se usa ConsoleNotifier.
import type { Alert } from "../contracts";

// Fila de la tabla `waiters` (03-contratos.md).
export interface Waiter {
  id: string;
  name: string;
  telegram_chat_id: string | null;
}

export interface SentMessage {
  channel: "telegram";
  chat_id: string;
  message_id: string;
}

export interface Notifier {
  send(alert: Alert, waiter: Waiter): Promise<SentMessage>;
  // Edita el mensaje según alert.status.
  update(alert: Alert, sent: SentMessage): Promise<void>;
}

// Notificador falso para desarrollo: imprime en consola y devuelve un message_id inventado.
export class ConsoleNotifier implements Notifier {
  private nextId = 1;

  async send(alert: Alert, waiter: Waiter): Promise<SentMessage> {
    console.log(`[notify] -> ${waiter.name}: ${alert.message} (${alert.id})`);
    return { channel: "telegram", chat_id: waiter.telegram_chat_id ?? waiter.id, message_id: String(this.nextId++) };
  }

  async update(alert: Alert, sent: SentMessage): Promise<void> {
    console.log(`[notify] edit ${sent.chat_id}/${sent.message_id}: ${alert.id} -> ${alert.status}`);
  }
}
