const TYPES = new Set(['object', 'array', 'string', 'number', 'integer', 'boolean', 'null']);
const COMMON_KEYS = new Set(['type', 'description', 'enum', 'anyOf']);
const TYPE_KEYS = {
  object: ['properties', 'required', 'additionalProperties'],
  array: ['items', 'minItems', 'maxItems'],
  string: ['minLength', 'maxLength'],
  number: ['minimum', 'maximum'],
  integer: ['minimum', 'maximum'],
  boolean: [],
  null: [],
};

export class ToolError extends Error {
  constructor(code, message) {
    super(message);
    this.name = 'ToolError';
    this.code = code;
  }
}

function object(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
    && [Object.prototype, null].includes(Object.getPrototypeOf(value));
}

function invalidSchema(message) {
  throw new TypeError(`Unsupported tool schema: ${message}`);
}

function checkSchema(schema, depth = 0) {
  if (depth > 12 || !object(schema)) invalidSchema('schema must be a plain object with depth <= 12');
  if (schema.description !== undefined && typeof schema.description !== 'string') invalidSchema('description must be a string');
  if (schema.anyOf !== undefined) {
    if (Object.keys(schema).some(key => !['anyOf', 'description'].includes(key))) invalidSchema('anyOf cannot have sibling constraints');
    if (!Array.isArray(schema.anyOf) || schema.anyOf.length !== 2
      || schema.anyOf.filter(s => s?.type === 'null').length !== 1) invalidSchema('anyOf supports exactly one schema plus null');
    for (const child of schema.anyOf) checkSchema(child, depth + 1);
    return;
  }
  const types = Array.isArray(schema.type) ? schema.type : [schema.type];
  if (types.some(type => !TYPES.has(type)) || new Set(types).size !== types.length) invalidSchema('unknown or repeated type');
  if (Array.isArray(schema.type) && (types.length !== 2 || !types.includes('null'))) invalidSchema('type arrays support one type plus null');
  const type = types.find(t => t !== 'null') ?? 'null';
  const keys = new Set([...COMMON_KEYS, ...TYPE_KEYS[type]]);
  if (Object.keys(schema).some(key => !keys.has(key))) invalidSchema(`unsupported keyword for ${type}`);
  if (schema.enum !== undefined) {
    if (!Array.isArray(schema.enum) || !schema.enum.length || schema.enum.length > 100) invalidSchema('enum must contain 1–100 values');
    if (schema.enum.some(value => value !== null && !['string', 'boolean', 'number'].includes(typeof value))) invalidSchema('enum supports primitive values');
    for (const value of schema.enum) {
      if (!matchesType(value, schema.type) || (typeof value === 'number' && !Number.isFinite(value))) invalidSchema('enum value must match type');
    }
    if (new Set(schema.enum).size !== schema.enum.length) invalidSchema('enum values must be unique');
  }
  if (type === 'object') {
    if (!object(schema.properties) || schema.additionalProperties !== false || !Array.isArray(schema.required)) {
      invalidSchema('objects need properties, required, and additionalProperties: false');
    }
    const names = Object.keys(schema.properties);
    if (names.length > 50 || schema.required.length !== names.length || new Set(schema.required).size !== names.length
      || schema.required.some(key => typeof key !== 'string' || !Object.hasOwn(schema.properties, key))) {
      invalidSchema('all properties must be required; use null for optional values');
    }
    for (const child of Object.values(schema.properties)) checkSchema(child, depth + 1);
  }
  if (type === 'array') checkSchema(schema.items, depth + 1);
  for (const [min, max] of [['minLength', 'maxLength'], ['minItems', 'maxItems'], ['minimum', 'maximum']]) {
    for (const key of [min, max]) {
      if (schema[key] === undefined) continue;
      if (!Number.isFinite(schema[key])) invalidSchema(`${key} must be finite`);
      if (!['minimum', 'maximum'].includes(key) && (!Number.isSafeInteger(schema[key]) || schema[key] < 0)) invalidSchema(`${key} must be a nonnegative integer`);
    }
    if (schema[min] !== undefined && schema[max] !== undefined && schema[min] > schema[max]) invalidSchema(`${min} exceeds ${max}`);
  }
}

function matchesType(value, type) {
  if (Array.isArray(type)) return type.some(t => matchesType(value, t));
  if (type === 'null') return value === null;
  if (type === 'object') return object(value);
  if (type === 'array') return Array.isArray(value);
  if (type === 'integer') return Number.isSafeInteger(value);
  if (type === 'number') return typeof value === 'number' && Number.isFinite(value);
  return typeof value === type;
}

function validationError(schema, value, path = '$', depth = 0) {
  if (depth > 12) return `${path}: nesting is too deep`;
  if (schema.anyOf) {
    return schema.anyOf.some(child => validationError(child, value, path, depth + 1) === null)
      ? null : `${path}: value does not match the nullable schema`;
  }
  if (!matchesType(value, schema.type)) return `${path}: invalid type`;
  if (schema.enum && !schema.enum.includes(value)) return `${path}: value is not in enum`;
  if (value === null) return null;
  const type = Array.isArray(schema.type) ? schema.type.find(t => t !== 'null') : schema.type;
  if (type === 'object') {
    if (schema.required.some(key => !Object.hasOwn(value, key))) return `${path}: required property is missing`;
    if (Reflect.ownKeys(value).some(key => typeof key !== 'string' || !Object.hasOwn(schema.properties, key))) return `${path}: additional properties are not allowed`;
    for (const [key, child] of Object.entries(schema.properties)) {
      const error = validationError(child, value[key], `${path}.${key}`, depth + 1);
      if (error) return error;
    }
  }
  if (type === 'array') {
    if (value.length > 10000) return `${path}: array exceeds validation budget`;
    if (schema.minItems !== undefined && value.length < schema.minItems) return `${path}: too few items`;
    if (schema.maxItems !== undefined && value.length > schema.maxItems) return `${path}: too many items`;
    for (let i = 0; i < value.length; i++) {
      const error = validationError(schema.items, value[i], `${path}[${i}]`, depth + 1);
      if (error) return error;
    }
  }
  if (type === 'string') {
    const length = [...value].length;
    if (length > 100000) return `${path}: string exceeds validation budget`;
    if (schema.minLength !== undefined && length < schema.minLength) return `${path}: string is too short`;
    if (schema.maxLength !== undefined && length > schema.maxLength) return `${path}: string is too long`;
  }
  if (type === 'number' || type === 'integer') {
    if (schema.minimum !== undefined && value < schema.minimum) return `${path}: number is below minimum`;
    if (schema.maximum !== undefined && value > schema.maximum) return `${path}: number exceeds maximum`;
  }
  return null;
}

const failure = (code, message) => ({ ok: false, error: { code, message } });

/** Local todo writes are staged per call. External side effects must honor context.signal. */
export class ToolRegistry {
  #tools = new Map();

  constructor({ timeoutMs = 5000, maxResultChars = 16000 } = {}) {
    if (!Number.isSafeInteger(timeoutMs) || timeoutMs < 1 || timeoutMs > 300000) throw new TypeError('Invalid tool timeout');
    if (!Number.isSafeInteger(maxResultChars) || maxResultChars < 100) throw new TypeError('Invalid result limit');
    this.timeoutMs = timeoutMs;
    this.maxResultChars = maxResultChars;
  }

  register({ name, description, parameters, execute }) {
    if (typeof name !== 'string' || !/^[A-Za-z_][A-Za-z0-9_-]{0,63}$/.test(name)) throw new TypeError('Invalid tool name');
    if (this.#tools.has(name)) throw new TypeError(`Duplicate tool: ${name}`);
    if (typeof description !== 'string' || !description.trim() || description.length > 2000) throw new TypeError('Invalid tool description');
    if (typeof execute !== 'function') throw new TypeError('Tool execute must be a function');
    checkSchema(parameters);
    if (parameters.type !== 'object') invalidSchema('tool parameters must be an object');
    if (JSON.stringify(parameters).length > 16000) invalidSchema('schema is too large');
    this.#tools.set(name, { name, description, parameters: structuredClone(parameters), execute });
    return this;
  }

  schemas() {
    return [...this.#tools.values()].map(({ name, description, parameters }) => ({
      type: 'function', function: { name, description, parameters: structuredClone(parameters), strict: true },
    }));
  }

  async execute(name, args, context = {}) {
    const tool = this.#tools.get(name);
    if (!tool) return failure('UNKNOWN_TOOL', 'Unknown tool');
    const invalid = validationError(tool.parameters, args);
    if (invalid) return failure('INVALID_ARGUMENTS', invalid.slice(0, 300));
    if (context.signal?.aborted) return failure('ABORTED', 'Tool call was cancelled');
    const controller = new AbortController();
    let timer;
    let abort;
    const cancelled = new Promise((_, reject) => {
      abort = () => {
        controller.abort();
        reject(new ToolError('ABORTED', 'Tool call was cancelled'));
      };
      context.signal?.addEventListener('abort', abort, { once: true });
      timer = setTimeout(() => {
        controller.abort();
        reject(new ToolError('TOOL_TIMEOUT', 'Tool execution timed out'));
      }, this.timeoutMs);
    });
    try {
      // Custom handlers receive an isolated snapshot. A late continuation after timeout
      // cannot touch the caller's session; only successful todo state is copied back.
      const sessionSnapshot = context.session === undefined ? undefined : structuredClone(context.session);
      const data = await Promise.race([
        Promise.resolve().then(() => {
          controller.signal.throwIfAborted();
          return tool.execute(structuredClone(args), { ...context, session: sessionSnapshot, signal: controller.signal });
        }),
        cancelled,
      ]);
      const serialized = JSON.stringify(data);
      if (serialized === undefined || serialized.length > this.maxResultChars) return failure('OUTPUT_TOO_LARGE', 'Tool result exceeds the output limit');
      const result = JSON.parse(serialized);
      controller.signal.throwIfAborted();
      if (sessionSnapshot !== undefined && (Object.hasOwn(sessionSnapshot, 'todos') || Object.hasOwn(context.session, 'todos'))) {
        if (!Array.isArray(sessionSnapshot.todos)) return failure('INVALID_SESSION', 'Session todos must be an array');
        context.session.todos = structuredClone(sessionSnapshot.todos);
      }
      return { ok: true, data: result };
    } catch (error) {
      if (error instanceof ToolError) return failure(String(error.code).slice(0, 50), String(error.message).replace(/[\u0000-\u001f]/g, ' ').slice(0, 300));
      return failure('TOOL_ERROR', 'Tool execution failed');
    } finally {
      clearTimeout(timer);
      context.signal?.removeEventListener('abort', abort);
    }
  }
}
