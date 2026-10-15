// Chrome messages use JSON; sandbox MessagePorts use structured clone. Preserve binary values at that boundary.
export async function encodeBrowserValue(value, ancestors = new Set()) {
  if (value === undefined) return { fibona_wire: "undefined" };
  if (value instanceof File) return { fibona_wire: "file", name: value.name, lastModified: value.lastModified, type: value.type, bytes: Array.from(new Uint8Array(await value.arrayBuffer())) };
  if (value instanceof Blob) return { fibona_wire: "blob", type: value.type, bytes: Array.from(new Uint8Array(await value.arrayBuffer())) };
  if (value instanceof ArrayBuffer) return { fibona_wire: "buffer", bytes: Array.from(new Uint8Array(value)) };
  if (ArrayBuffer.isView(value)) return { fibona_wire: "typed", type: value.constructor.name, bytes: Array.from(new Uint8Array(value.buffer, value.byteOffset, value.byteLength)) };
  if (value instanceof Date) return { fibona_wire: "date", value: Number.isNaN(value.getTime()) ? null : value.getTime() };
  if (typeof value === "number" && !Number.isFinite(value)) return { fibona_wire: "number", value: String(value) };
  if (typeof value === "bigint") return { fibona_wire: "bigint", value: String(value) };
  if (typeof value === "function" || typeof value === "symbol") throw new TypeError("Fibona messages cannot carry functions or symbols; keep callbacks in their own context");
  if (value && typeof value === "object") {
    if (ancestors.has(value)) throw new TypeError("Fibona Chrome messages cannot carry cyclic data; send explicit state without cycles");
    const next = new Set(ancestors).add(value);
    if (Array.isArray(value)) return { fibona_wire: "array", length: value.length, entries: await Promise.all(Object.entries(value).map(async ([key, item]) => [key, await encodeBrowserValue(item, next)])) };
    if (value instanceof Map) return { fibona_wire: "map", entries: await Promise.all([...value].map(async ([key, item]) => [await encodeBrowserValue(key, next), await encodeBrowserValue(item, next)])) };
    if (value instanceof Set) return { fibona_wire: "set", values: await Promise.all([...value].map(item => encodeBrowserValue(item, next))) };
    if (Object.getPrototypeOf(value) !== Object.prototype && Object.getPrototypeOf(value) !== null) throw new TypeError("Fibona Chrome messages need data, not host handles or class instances");
    return { fibona_wire: "object", entries: await Promise.all(Object.entries(value).map(async ([key, item]) => [key, await encodeBrowserValue(item, next)])) };
  }
  return value;
}

// Envelope ordinary user objects too, so fibona_wire keys in user data cannot be mistaken for a type tag.
export function decodeBrowserValue(value) {
  switch (value?.fibona_wire) {
    case "undefined": return undefined;
    case "file": return new File([new Uint8Array(value.bytes)], value.name, { type: value.type, lastModified: value.lastModified });
    case "blob": return new Blob([new Uint8Array(value.bytes)], { type: value.type });
    case "buffer": return new Uint8Array(value.bytes).buffer;
    case "typed": {
      const buffer = new Uint8Array(value.bytes).buffer;
      const types = { Uint8Array, Uint8ClampedArray, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array, Float32Array, Float64Array, BigInt64Array, BigUint64Array, DataView };
      return new (types[value.type] ?? Uint8Array)(buffer);
    }
    case "date": return new Date(value.value ?? NaN);
    case "bigint": return BigInt(value.value);
    case "number": return Number(value.value);
    case "map": return new Map(value.entries.map(([key, item]) => [decodeBrowserValue(key), decodeBrowserValue(item)]));
    case "set": return new Set(value.values.map(decodeBrowserValue));
    case "array": {
      const array = new Array(value.length);
      for (const [key, item] of value.entries) Object.defineProperty(array, key, { value: decodeBrowserValue(item), enumerable: true, writable: true, configurable: true });
      return array;
    }
    case "object": return Object.fromEntries(value.entries.map(([key, item]) => [key, decodeBrowserValue(item)]));
    default: return value;
  }
}
