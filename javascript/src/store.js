import { createHash, randomUUID } from 'node:crypto';
import { mkdir, readFile, readdir, rename, unlink, writeFile } from 'node:fs/promises';
import path from 'node:path';

function failure(code, message, cause) {
  return Object.assign(new Error(message, cause ? { cause } : undefined), { code });
}

function validateId(value, label) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9][A-Za-z0-9_.:@-]{0,127}$/.test(value)) {
    throw failure('INVALID_ID', `${label} must contain 1–128 letters, digits, or . _ : @ - and start with a letter or digit.`);
  }
}

const hash = (value) => createHash('sha256').update(value).digest('hex');
const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const isTimestamp = (value) => typeof value === 'string' && Number.isFinite(Date.parse(value));

function validHistory(messages) {
  const pending = new Set();
  let lastWasFinal = true;
  for (let index = 0; index < messages.length; index += 1) {
    const message = messages[index];
    if (!isObject(message) || !['user', 'assistant', 'tool'].includes(message.role)
        || (index === 0 && message.role !== 'user')) return false;
    if (message.role === 'user') {
      if (pending.size || !lastWasFinal || typeof message.content !== 'string' || message.tool_calls !== undefined) return false;
      lastWasFinal = false;
    } else if (message.role === 'assistant') {
      if (pending.size || (message.content !== null && typeof message.content !== 'string')
          || (message.tool_calls !== undefined && !Array.isArray(message.tool_calls))) return false;
      for (const call of message.tool_calls ?? []) {
        if (!isObject(call) || call.type !== 'function' || typeof call.id !== 'string' || !call.id
            || pending.has(call.id) || !isObject(call.function)
            || typeof call.function.name !== 'string' || !call.function.name
            || typeof call.function.arguments !== 'string') return false;
        pending.add(call.id);
      }
      lastWasFinal = pending.size === 0 && typeof message.content === 'string';
    } else {
      if (typeof message.content !== 'string' || message.tool_calls !== undefined
          || !pending.delete(message.tool_call_id)) return false;
      lastWasFinal = false;
    }
  }
  return !pending.size && lastWasFinal;
}

function validateSession(session) {
  if (!isObject(session)) throw failure('CORRUPT_SESSION', 'Session must be an object.');
  validateId(session.userId, 'userId');
  validateId(session.id, 'sessionId');
  if (typeof session.title !== 'string' || session.title.length > 200 || typeof session.summary !== 'string'
      || !Array.isArray(session.messages) || !Array.isArray(session.todos)
      || !isObject(session.completedRequests)
      || !isTimestamp(session.createdAt) || !isTimestamp(session.updatedAt)) {
    throw failure('CORRUPT_SESSION', 'Session has invalid or missing fields.');
  }
  if (!validHistory(session.messages) || session.todos.some((todo) => !isObject(todo)
      || typeof todo.id !== 'string' || !todo.id || typeof todo.text !== 'string'
      || typeof todo.done !== 'boolean' || (todo.createdAt !== undefined && !isTimestamp(todo.createdAt)))
      || Object.values(session.completedRequests).some((record) => !isObject(record))) {
    throw failure('CORRUPT_SESSION', 'Session contains an invalid message, todo, or replay record.');
  }
  return session;
}

/** File persistence for one server process. Call withLock around an entire read/modify/save turn. */
export class SessionStore {
  constructor({ dataDir } = {}) {
    if (typeof dataDir !== 'string' || !dataDir) throw new TypeError('dataDir is required.');
    this.dataDir = path.resolve(dataDir);
    this.locks = new Map();
  }

  filePath(userId, sessionId) {
    validateId(userId, 'userId');
    validateId(sessionId, 'sessionId');
    return path.join(this.dataDir, `${hash(userId)}-${hash(sessionId)}.json`);
  }

  async readExisting(file, userId, sessionId) {
    let raw;
    try {
      raw = await readFile(file, 'utf8');
    } catch (error) {
      if (error.code === 'ENOENT') return null;
      throw error;
    }
    try {
      const session = validateSession(JSON.parse(raw));
      if (session.userId !== userId || (sessionId !== undefined && session.id !== sessionId)
          || this.filePath(session.userId, session.id) !== file) {
        throw new Error('Session identity does not match its storage key.');
      }
      return session;
    } catch (error) {
      throw failure('CORRUPT_SESSION', 'Stored session is corrupt; the file has been preserved.', error);
    }
  }

  async create(userId, title = 'New conversation') {
    validateId(userId, 'userId');
    if (typeof title !== 'string' || title.length > 200) {
      throw failure('INVALID_TITLE', 'title must be a string of at most 200 characters.');
    }
    const now = new Date().toISOString();
    const session = {
      id: randomUUID(), userId, title: title.trim() || 'New conversation',
      createdAt: now, updatedAt: now, messages: [], summary: '', todos: [], completedRequests: {},
    };
    await this.save(session);
    return session;
  }

  async get(userId, sessionId) {
    const session = await this.readExisting(this.filePath(userId, sessionId), userId, sessionId);
    if (!session) throw failure('SESSION_NOT_FOUND', 'Session was not found for this user.');
    return session;
  }

  async list(userId) {
    validateId(userId, 'userId');
    let files;
    try {
      files = await readdir(this.dataDir);
    } catch (error) {
      if (error.code === 'ENOENT') return [];
      throw error;
    }
    const prefix = `${hash(userId)}-`;
    const sessions = [];
    for (const name of files.filter((name) => name.startsWith(prefix) && name.endsWith('.json'))) {
      const session = await this.readExisting(path.join(this.dataDir, name), userId);
      if (session) sessions.push(session);
    }
    return sessions.sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
  }

  async save(session) {
    validateSession(session);
    const file = this.filePath(session.userId, session.id);
    await mkdir(this.dataDir, { recursive: true });
    // Never replace a corrupt existing file with apparently fresh state.
    await this.readExisting(file, session.userId, session.id);
    session.updatedAt = new Date().toISOString();
    const temp = `${file}.${randomUUID()}.tmp`;
    try {
      await writeFile(temp, `${JSON.stringify(session, null, 2)}\n`, { encoding: 'utf8', flag: 'wx', mode: 0o600 });
      await rename(temp, file);
    } finally {
      await unlink(temp).catch((error) => { if (error.code !== 'ENOENT') throw error; });
    }
    return session;
  }

  async withLock(userId, sessionId, fn) {
    const key = this.filePath(userId, sessionId);
    if (typeof fn !== 'function') throw new TypeError('Lock callback is required.');
    const previous = this.locks.get(key) ?? Promise.resolve();
    const operation = previous.then(fn);
    // A failed request releases its lock and cannot poison later requests.
    const settled = operation.then(() => undefined, () => undefined);
    this.locks.set(key, settled);
    try {
      return await operation;
    } finally {
      if (this.locks.get(key) === settled) this.locks.delete(key);
    }
  }
}
