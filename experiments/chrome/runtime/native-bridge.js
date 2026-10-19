// Native objects by reference, across a message boundary: fibona_browser (the session to the offscreen document),
// fibona_view (the app to its view page) and functions in Chrome event arguments (the service worker to the session).
// Data is copied; objects with their own class and functions stay with their owner and cross as references, whose
// methods (get, set, call, construct, release) send requests back. No browser API is named here.
// A classic script, so the sandboxed pages can load it with <script> and modules can import it.
class FibonaNativeBridge {
  // send(message): posts to the other side's bridge. root: the object reference 0 resolves to, on the owning side.
  constructor(send, root) {
    this.send = send;
    this.sequence = 0;
    this.namespace = crypto.randomUUID();  // Reference ids are namespaced, so two bridges' ids never collide.
    this.references = new Map([[0, { value: root }]]);  // Id -> {value, owner}: what this side lends out.
    this.identities = new WeakMap();  // Value -> id, so lending the same object twice gives the same reference.
    this.remote = new WeakMap();  // Reference object -> id: what this side borrowed.
    this.remoteIdentities = new Map();  // Id -> reference object.
    this.pending = new Map();
    this.closed = false;
  }

  // A value as a message node: data is copied, cycles included; native objects and functions become references.
  encode(value, seen = new Map()) {
    if (value === null || (typeof value !== "object" && typeof value !== "function")) return { kind: "value", value };
    if (this.remote.has(value)) return { kind: "return", id: this.remote.get(value) };  // Back to its owner.
    if (value instanceof Blob || value instanceof ArrayBuffer || ArrayBuffer.isView(value) || value instanceof Date) {
      return { kind: "value", value };  // Structured clone and browser-wire.js copy these.
    }
    const plain = [Object.prototype, Array.prototype, Map.prototype, Set.prototype, null].includes(Object.getPrototypeOf(value));
    if (typeof value === "function" || !plain) {
      let id = this.identities.get(value);
      if (id === undefined) {
        id = `${this.namespace}:${++this.sequence}`;
        this.references.set(id, { value });
        this.identities.set(value, id);
      }
      return { kind: "reference", id, callable: typeof value === "function" };
    }
    if (seen.has(value)) return { kind: "alias", id: seen.get(value) };
    const id = seen.size;
    seen.set(value, id);
    const entries = (pairs) => pairs.map(([key, item]) => [key, this.encode(item, seen)]);
    if (Array.isArray(value)) return { kind: "array", id, length: value.length, entries: entries(Object.entries(value)) };
    if (value instanceof Map) return { kind: "map", id, entries: [...value].map(([k, v]) => [this.encode(k, seen), this.encode(v, seen)]) };
    if (value instanceof Set) return { kind: "set", id, values: [...value].map((item) => this.encode(item, seen)) };
    return { kind: "object", id, entries: entries(Object.entries(value)) };
  }

  decode(node, seen = new Map()) {
    if (node.kind === "value") return node.value;
    if (node.kind === "alias") return seen.get(node.id);
    if (node.kind === "return") {
      const entry = this.references.get(node.id);
      if (!entry) throw new Error("Fibona: this native reference was released");
      return entry.value;
    }
    if (node.kind === "reference") return this.reference(node);
    const value = { array: () => new Array(node.length), map: () => new Map(), set: () => new Set() }[node.kind]?.() ?? {};
    seen.set(node.id, value);
    if (node.kind === "map") for (const [key, item] of node.entries) value.set(this.decode(key, seen), this.decode(item, seen));
    else if (node.kind === "set") for (const item of node.values) value.add(this.decode(item, seen));
    else {
      // defineProperty keeps keys such as __proto__ as data, and leaves the holes of sparse arrays.
      for (const [key, item] of node.entries) {
        Object.defineProperty(value, key, { value: this.decode(item, seen), enumerable: true, writable: true, configurable: true });
      }
    }
    return value;
  }

  // A borrowed object or function. Calling a borrowed function invokes the original, asynchronously.
  reference({ id, callable }) {
    if (this.remoteIdentities.has(id)) return this.remoteIdentities.get(id);
    const reference = callable ? (...args) => this.request("invoke", id, "", args) : {};
    Object.assign(reference, {
      get: (path) => this.request("get", id, path),
      set: (path, value) => this.request("set", id, path, [value]),
      call: (path, ...args) => this.request("call", id, path, args),
      construct: (path, ...args) => this.request("construct", id, path, args),
      release: async () => {
        this.remoteIdentities.delete(id);
        this.remote.delete(reference);
        await this.request("release", id, "");
      },
    });
    this.remote.set(reference, id);
    this.remoteIdentities.set(id, reference);
    return reference;
  }

  request(operation, reference, path, args = []) {
    if (this.closed) return Promise.reject(new Error("Fibona: the native bridge is closed"));
    const id = ++this.sequence;
    return new Promise((resolve, reject) => {
      this.pending.set(id, { resolve, reject });
      Promise.resolve()
        .then(() => this.send({ type: "call", id, operation, reference, path, args: this.encode(args) }))
        .catch((error) => {
          this.pending.delete(id);
          reject(error);
        });
    });
  }

  // A message from the other side: a reply to one of our requests, or a call on something we lent.
  // reply(message) answers the call; by default it goes back through send.
  async receive(message, reply = this.send) {
    if (this.closed) return;
    if (message.type === "reply") {
      const waiter = this.pending.get(message.id);
      if (!waiter) return;
      this.pending.delete(message.id);
      if (message.error !== undefined) waiter.reject(Object.assign(new Error(message.error), { name: message.errorName }));
      else {
        try {
          waiter.resolve(this.decode(message.value));
        } catch (error) {
          waiter.reject(error);
        }
      }
      return;
    }
    let answer;
    try {
      answer = { type: "reply", id: message.id, value: await this.perform(message) };
    } catch (error) {
      answer = { type: "reply", id: message.id, error: error?.message ?? String(error), errorName: error?.name ?? "Error" };
    }
    try {
      await reply(answer);
    } catch (error) {
      await Promise.resolve().then(() => reply({ type: "reply", id: message.id,
        error: error?.message ?? String(error) })).catch(() => {});
    }
  }

  async perform({ operation, reference, path, args }) {
    const entry = this.references.get(reference);
    if (!entry) throw new Error("Fibona: this native reference was released");
    if (operation === "release") {
      this.identities.delete(entry.value);
      this.references.delete(reference);
      return this.encode(undefined);
    }
    const values = this.decode(args);
    const parts = path ? path.split(".") : [];
    const key = parts.pop();
    let owner = entry.value;
    for (const part of parts) owner = Reflect.get(owner, part, owner);
    let value;
    if (operation === "get") value = Reflect.get(owner, key, owner);
    else if (operation === "set") {
      if (!Reflect.set(owner, key, values[0], owner)) throw new TypeError(`Fibona: cannot set ${key}`);
    } else if (operation === "call") value = Reflect.apply(Reflect.get(owner, key, owner), owner, values);
    else if (operation === "construct") value = Reflect.construct(Reflect.get(owner, key, owner), values);
    else if (operation === "invoke") value = Reflect.apply(entry.value, entry.owner, values);
    else throw new Error(`Fibona: unknown native operation ${operation}`);
    value = await value;
    // A method read with get keeps its receiver, so calling it later still works.
    if (operation === "get" && typeof value === "function") {
      const id = `${this.namespace}:${++this.sequence}`;
      this.references.set(id, { value, owner });
      return { kind: "reference", id, callable: true };
    }
    return this.encode(value);
  }

  // The other side's root object: call(path, ...args), get, set, construct, and release(reference) for anything
  // borrowed through it, or for a function lent to it once it is no longer registered anywhere.
  root() {
    const call = (path, ...args) => this.request("call", 0, path, args);
    return Object.assign(call, {
      get: (path) => this.request("get", 0, path),
      set: (path, value) => this.request("set", 0, path, [value]),
      construct: (path, ...args) => this.request("construct", 0, path, args),
      release: async (value) => {
        if (this.remote.has(value)) return await value.release();
        const id = this.identities.get(value);
        if (id === undefined) return;
        this.references.delete(id);
        this.identities.delete(value);
      },
    });
  }

  close() {
    this.closed = true;
    for (const waiter of this.pending.values()) waiter.reject(new Error("Fibona: the native bridge is closed"));
    this.pending.clear();
    this.references.clear();
    this.remoteIdentities.clear();
  }
}
globalThis.FibonaNativeBridge = FibonaNativeBridge;
