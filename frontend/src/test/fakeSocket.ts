// Test-only WebSocket stand-in: tests push server messages by hand. Never imported by app code.
import { vi } from 'vitest';

export class FakeSocket {
  static instances: FakeSocket[] = [];
  onmessage: ((event: MessageEvent<string>) => void) | null = null;
  onclose: (() => void) | null = null;
  closed = false;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  /** Deliver one message as if the server sent it. */
  push(payload: unknown) {
    const data = typeof payload === 'string' ? payload : JSON.stringify(payload);
    this.onmessage?.(new MessageEvent('message', { data }));
  }

  /** Simulate the server dropping the connection. */
  drop() {
    this.onclose?.();
  }

  close() {
    this.closed = true;
  }
}

export function stubSocket() {
  FakeSocket.instances = [];
  vi.stubGlobal('WebSocket', FakeSocket);
  return FakeSocket.instances;
}
