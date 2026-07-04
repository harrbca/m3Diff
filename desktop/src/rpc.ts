// NDJSON RPC client (frontend side of ADR-001). Sends request lines to the Rust
// backend via the `rpc_send` command and correlates the `rpc://message` event
// frames back by id. Mirrors engine/src/m3diff/rpc.py.
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";

export interface Progress {
  done: number;
  total: number;
  table: string;
}

export class RpcCancelled extends Error {
  constructor() {
    super("cancelled");
    this.name = "RpcCancelled";
  }
}

interface Pending {
  resolve: (value: unknown) => void;
  reject: (error: unknown) => void;
  onProgress?: (p: Progress) => void;
}

export interface Handle<T> {
  id: number;
  done: Promise<T>;
}

class Rpc {
  private nextId = 1;
  private pending = new Map<number, Pending>();
  private ready: Promise<unknown>;

  constructor() {
    this.ready = listen<string>("rpc://message", (event) => this.receive(event.payload));
  }

  private receive(line: string): void {
    let msg: any;
    try {
      msg = JSON.parse(line);
    } catch {
      return;
    }
    const pending = this.pending.get(msg.id);
    if (!pending) return;
    if (msg.type === "progress") {
      pending.onProgress?.(msg.progress as Progress);
      return;
    }
    this.pending.delete(msg.id);
    if (msg.type === "result") pending.resolve(msg.result);
    else if (msg.type === "cancelled") pending.reject(new RpcCancelled());
    else pending.reject(new Error(msg.error?.message ?? "rpc error"));
  }

  start<T>(method: string, params: unknown = {}, onProgress?: (p: Progress) => void): Handle<T> {
    const id = this.nextId++;
    const done = new Promise<T>((resolve, reject) => {
      this.pending.set(id, { resolve: resolve as (v: unknown) => void, reject, onProgress });
    });
    this.ready
      .then(() => invoke("rpc_send", { line: JSON.stringify({ id, method, params }) }))
      .catch((err) => {
        const pending = this.pending.get(id);
        if (pending) {
          this.pending.delete(id);
          pending.reject(err);
        }
      });
    return { id, done };
  }

  request<T>(method: string, params?: unknown, onProgress?: (p: Progress) => void): Promise<T> {
    return this.start<T>(method, params ?? {}, onProgress).done;
  }

  ping(): Promise<{ pong: boolean; version: string }> {
    return this.request("ping");
  }

  cancel(targetId: number): Promise<unknown> {
    return this.request("cancel", { target_id: targetId });
  }
}

export const rpc = new Rpc();
