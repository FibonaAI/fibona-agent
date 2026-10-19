// Requests with replies over one message transport: a MessagePort, a chrome.runtime Port or window.postMessage.
// `send(message)` posts a message; feed every incoming one to `receive`. A request runs `handlers[method](params)`
// on the other side and resolves to its result; any other message goes to `handlers.message`.
// A classic script, so the sandboxed pages can load it with <script> and modules can import it.
class FibonaChannel {
  constructor(send, handlers = {}) {
    this.send = send;
    this.handlers = handlers;
    this.sequence = 0;
    this.pending = new Map();  // Request id -> {resolve, reject}
    this.closed = false;
  }

  request(method, params = {}) {
    if (this.closed) return Promise.reject(new Error("Fibona: the channel is closed"));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      Promise.resolve()
        .then(() => this.send({ type: "request", id, method, params }))
        .catch((error) => {
          this.pending.delete(id);
          reject(error);
        });
    });
  }

  async receive(message) {
    if (message?.type === "reply") {
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error === undefined) waiter.resolve(message.value);
      else waiter.reject(Object.assign(new Error(message.error), { name: message.errorName ?? "Error" }));
    } else if (message?.type === "request") {
      let reply;
      try {
        const handler = this.handlers[message.method];
        if (!handler) throw new Error(`Fibona: nothing handles ${message.method}`);
        reply = { type: "reply", id: message.id, value: await handler(message.params) };
      } catch (error) {
        reply = { type: "reply", id: message.id, error: error?.message ?? String(error), errorName: error?.name };
      }
      try {
        await this.send(reply);
      } catch (error) {
        // Encoding a result can fail while the peer is still connected. Return that failure as plain data.
        await Promise.resolve().then(() => this.send({ type: "reply", id: message.id,
          error: error?.message ?? String(error) })).catch(() => {});
      }
    } else {
      await this.handlers.message?.(message);
    }
  }

  // Rejects every pending request, as when the other side disconnects.
  close(reason = "Fibona: the channel is closed") {
    this.closed = true;
    for (const waiter of this.pending.values()) waiter.reject(new Error(reason));
    this.pending.clear();
  }
}
globalThis.FibonaChannel = FibonaChannel;
