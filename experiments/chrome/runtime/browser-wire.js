// chrome.runtime messages carry JSON, while MessagePorts carry structured clones. Values crossing the JSON
// boundary are wrapped so binary data, undefined, dates, maps, sets and sparse arrays arrive as they left.
// Every object is wrapped, so a user object with a fibona_wire key cannot pass for a wrapper.
const bytes = (buffer, offset = 0, length = buffer.byteLength) => Array.from(new Uint8Array(buffer, offset, length));

export async function encodeBrowserValue(value, ancestors = new Set()) {
  if (value === undefined) return { fibona_wire: "undefined" };
  if (typeof value === "number" && !Number.isFinite(value)) return { fibona_wire: "number", value: String(value) };
  if (typeof value === "bigint") return { fibona_wire: "bigint", value: String(value) };
  if (typeof value === "function" || typeof value === "symbol") {
    throw new TypeError("Fibona: functions and symbols cannot cross a Chrome message; keep callbacks where they are");
  }
  if (!value || typeof value !== "object") return value;
  if (value instanceof File) {
    return { fibona_wire: "file", name: value.name, type: value.type, lastModified: value.lastModified, bytes: bytes(await value.arrayBuffer()) };
  }
  if (value instanceof Blob) return { fibona_wire: "blob", type: value.type, bytes: bytes(await value.arrayBuffer()) };
  if (value instanceof ArrayBuffer) return { fibona_wire: "buffer", bytes: bytes(value) };
  if (ArrayBuffer.isView(value)) {
    return { fibona_wire: "typed", type: value.constructor.name, bytes: bytes(value.buffer, value.byteOffset, value.byteLength) };
  }
  if (value instanceof Date) return { fibona_wire: "date", value: Number.isNaN(value.getTime()) ? null : value.getTime() };
  if (ancestors.has(value)) throw new TypeError("Fibona: cyclic data cannot cross a Chrome message");
  const inner = new Set(ancestors).add(value);
  const encode = (item) => encodeBrowserValue(item, inner);
  const entries = (pairs) => Promise.all(pairs.map(async ([key, item]) => [key, await encode(item)]));
  if (Array.isArray(value)) return { fibona_wire: "array", length: value.length, entries: await entries(Object.entries(value)) };
  if (value instanceof Map) {
    return { fibona_wire: "map", entries: await Promise.all([...value].map(async ([k, v]) => [await encode(k), await encode(v)])) };
  }
  if (value instanceof Set) return { fibona_wire: "set", values: await Promise.all([...value].map(encode)) };
  if (![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    throw new TypeError("Fibona: Chrome messages carry data, not class instances or native objects");
  }
  return { fibona_wire: "object", entries: await entries(Object.entries(value)) };
}

const TYPED = { Uint8Array, Uint8ClampedArray, Uint16Array, Uint32Array, Int8Array, Int16Array, Int32Array, Float32Array,
  Float64Array, BigInt64Array, BigUint64Array, DataView };

export function decodeBrowserValue(value) {
  const buffer = () => new Uint8Array(value.bytes).buffer;
  const define = (target, key, item) =>
    Object.defineProperty(target, key, { value: decodeBrowserValue(item), enumerable: true, writable: true, configurable: true });
  switch (value?.fibona_wire) {
    case "undefined": return undefined;
    case "number": return Number(value.value);
    case "bigint": return BigInt(value.value);
    case "file": return new File([buffer()], value.name, { type: value.type, lastModified: value.lastModified });
    case "blob": return new Blob([buffer()], { type: value.type });
    case "buffer": return buffer();
    case "typed": return new (TYPED[value.type] ?? Uint8Array)(buffer());
    case "date": return new Date(value.value ?? NaN);
    case "map": return new Map(value.entries.map(([k, v]) => [decodeBrowserValue(k), decodeBrowserValue(v)]));
    case "set": return new Set(value.values.map(decodeBrowserValue));
    case "array": {
      const array = new Array(value.length);
      for (const [key, item] of value.entries) define(array, key, item);
      return array;
    }
    case "object": {
      const object = {};
      for (const [key, item] of value.entries) define(object, key, item);
      return object;
    }
    default: return value;
  }
}
