// Reference RPC: data is cloned, native objects stay with their owner, and functions become async callbacks.
// No browser API names or capability-specific adapters belong in this protocol.
class FibonaNativeBridge {
  constructor(send, root) {
    this.send = send;
    this.sequence = 0;
    this.references = new Map([[0, { value: root }]]);
    this.identities = new WeakMap();
    this.remote = new WeakMap();
    this.remoteIdentities = new Map();
    this.pending = new Map();
    this.closed = false;
  }
  encode(value, seen = new Map()) {
    if (value === null || !["object", "function"].includes(typeof value)) return { kind: "value", value };
    if (this.remote.has(value)) return { kind: "return", id: this.remote.get(value) };
    if (typeof value === "function" || ![Object.prototype, null, Array.prototype, Map.prototype, Set.prototype].includes(Object.getPrototypeOf(value))) {
      if (value instanceof Blob || value instanceof ArrayBuffer || ArrayBuffer.isView(value) || value instanceof Date) return { kind: "value", value };
      let id = this.identities.get(value);
      if (id === undefined) { id = ++this.sequence; this.references.set(id, { value }); this.identities.set(value, id); }
      return { kind: "reference", id, callable: typeof value === "function" };
    }
    if (seen.has(value)) return { kind: "alias", id: seen.get(value) };
    const id = seen.size; seen.set(value, id);
    if (Array.isArray(value)) return { kind: "array", id, length: value.length, entries: Object.entries(value).map(([key, item]) => [key, this.encode(item, seen)]) };
    if (value instanceof Map) return { kind: "map", id, entries: [...value].map(([key, item]) => [this.encode(key, seen), this.encode(item, seen)]) };
    if (value instanceof Set) return { kind: "set", id, values: [...value].map(item => this.encode(item, seen)) };
    return { kind: "object", id, entries: Object.entries(value).map(([key, item]) => [key, this.encode(item, seen)]) };
  }
  decode(node, seen = new Map()) {
    if (node.kind === "value") return node.value;
    if (node.kind === "return") {
      if (!this.references.has(node.id)) throw new Error("Fibona native reference was released");
      return this.references.get(node.id).value;
    }
    if (node.kind === "reference") {
      if (this.remoteIdentities.has(node.id)) return this.remoteIdentities.get(node.id);
      const reference = node.callable ? (...args) => this.closed ? undefined : this.request("invoke", node.id, "", args) : {};
      Object.assign(reference, {
        get: path => this.request("get", node.id, path),
        set: (path, value) => this.request("set", node.id, path, [value]),
        call: (path, ...args) => this.request("call", node.id, path, args),
        construct: (path, ...args) => this.request("construct", node.id, path, args),
        release: async () => { await this.request("release", node.id, ""); this.remoteIdentities.delete(node.id); this.remote.delete(reference); },
      });
      this.remote.set(reference, node.id);
      this.remoteIdentities.set(node.id, reference);
      return reference;
    }
    if (node.kind === "alias") return seen.get(node.id);
    const value = node.kind === "array" ? new Array(node.length) : node.kind === "map" ? new Map() : node.kind === "set" ? new Set() : {};
    seen.set(node.id, value);
    if (node.kind === "map") for (const [key, item] of node.entries) value.set(this.decode(key, seen), this.decode(item, seen));
    else if (node.kind === "set") for (const item of node.values) value.add(this.decode(item, seen));
    else for (const [key, item] of node.entries) Object.defineProperty(value, key, { value: this.decode(item, seen), enumerable: true, writable: true, configurable: true });
    return value;
  }
  request(operation, reference, path, args = []) {
    if (this.closed) return Promise.reject(new Error("Fibona native bridge closed"));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      try { Promise.resolve(this.send({ type: "call", id, operation, reference, path, args: this.encode(args) })).catch(error => { this.pending.delete(id); reject(error); }); }
      catch (error) { this.pending.delete(id); reject(error); }
    });
  }
  async receive(message) {
    if (this.closed) return;
    if (message.type === "reply") {
      const waiter = this.pending.get(message.id); if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error) waiter.reject(Object.assign(new Error(message.error), { name: message.errorName }));
      else { try { waiter.resolve(this.decode(message.value)); } catch (error) { waiter.reject(error); } }
      return;
    }
    try {
      const entry = this.references.get(message.reference);
      if (!entry) throw new Error("Fibona native reference was released");
      const args = this.decode(message.args);
      let owner = entry.value, value;
      const parts = message.path ? message.path.split(".") : [];
      const key = parts.pop();
      for (const part of parts) owner = Reflect.get(owner, part, owner);
      if (message.operation === "get") value = Reflect.get(owner, key, owner);
      else if (message.operation === "set") { if (!Reflect.set(owner, key, args[0], owner)) throw new TypeError("Fibona cannot set native property: " + key); }
      else if (message.operation === "call") value = Reflect.apply(Reflect.get(owner, key, owner), owner, args);
      else if (message.operation === "construct") value = Reflect.construct(Reflect.get(owner, key, owner), args);
      else if (message.operation === "invoke") value = Reflect.apply(entry.value, entry.owner, args);
      else if (message.operation === "release") { this.identities.delete(entry.value); this.references.delete(message.reference); }
      else throw new Error("Fibona native bridge operation unavailable: " + message.operation);
      value = await value;
      // A retrieved native method must retain its receiver when invoked later.
      if (message.operation === "get" && typeof value === "function") {
        const id = ++this.sequence; this.references.set(id, { value, owner });
        await this.send({ type: "reply", id: message.id, value: { kind: "reference", id, callable: true } });
      } else await this.send({ type: "reply", id: message.id, value: this.encode(value) });
    } catch (error) {
      await Promise.resolve(this.send({ type: "reply", id: message.id, error: error?.message ?? String(error), errorName: error?.name ?? "Error" })).catch(() => {});
    }
  }
  root() {
    const call = (path, ...args) => this.request("call", 0, path, args);
    return Object.assign(call, {
      get: path => this.request("get", 0, path),
      set: (path, value) => this.request("set", 0, path, [value]),
      construct: (path, ...args) => this.request("construct", 0, path, args),
      release: async value => {
        if (this.remote.has(value)) return await value.release();
        const id = this.identities.get(value);
        if (id !== undefined) { this.references.delete(id); this.identities.delete(value); }
      },
    });
  }
  close() {
    this.closed = true;
    for (const waiter of this.pending.values()) waiter.reject(new Error("Fibona native bridge closed"));
    this.pending.clear(); this.references.clear(); this.remoteIdentities.clear();
  }
}
globalThis.FibonaNativeBridge = FibonaNativeBridge;
