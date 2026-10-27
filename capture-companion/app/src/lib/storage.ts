/** Pluggable key-value storage for records and evidence binaries.
 *
 * The browser backend is IndexedDB (persistent, Blob-capable). The memory
 * backend is for tests and for environments without storage. Binary photos
 * are NEVER written to localStorage; UI preferences alone may use it.
 */

export type StoreName = 'sessions' | 'observations' | 'images' | 'binaries' | 'meta';

export type StorageFailure =
  | 'unavailable'
  | 'quota-exceeded'
  | 'interrupted'
  | 'unknown';

export class StorageError extends Error {
  readonly kind: StorageFailure;

  constructor(kind: StorageFailure, message: string, options?: { cause?: unknown }) {
    super(message);
    this.name = 'StorageError';
    this.kind = kind;
    if (options?.cause !== undefined) (this as { cause?: unknown }).cause = options.cause;
  }
}

export interface StorageBackend {
  readonly name: 'indexeddb' | 'memory';
  get<T>(store: StoreName, key: string): Promise<T | undefined>;
  put(store: StoreName, key: string, value: unknown): Promise<void>;
  remove(store: StoreName, key: string): Promise<void>;
  all<T>(store: StoreName): Promise<Array<{ key: string; value: T }>>;
  clear(store: StoreName): Promise<void>;
  close(): void;
}

function classifyWriteError(error: unknown): StorageError {
  const name =
    typeof error === 'object' && error !== null && 'name' in error
      ? String((error as { name: unknown }).name)
      : '';
  if (name === 'QuotaExceededError') {
    return new StorageError(
      'quota-exceeded',
      'Device storage is full: the capture was NOT saved. Free space or export and delete old sessions, then retry.',
      { cause: error },
    );
  }
  return new StorageError('interrupted', `Storage write was interrupted (${name || 'unknown error'})`, {
    cause: error,
  });
}

// ---------------------------------------------------------------------------
// IndexedDB backend
// ---------------------------------------------------------------------------

const DB_NAME = 'anastylos-capture';
const DB_VERSION = 1;

const STORES: StoreName[] = ['sessions', 'observations', 'images', 'binaries', 'meta'];

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    if (typeof indexedDB === 'undefined') {
      reject(new StorageError('unavailable', 'IndexedDB is not available in this browser.'));
      return;
    }
    let request: IDBOpenDBRequest;
    try {
      request = indexedDB.open(DB_NAME, DB_VERSION);
    } catch (error) {
      reject(
        new StorageError('unavailable', 'IndexedDB could not be opened in this browser.', {
          cause: error,
        }),
      );
      return;
    }
    request.onupgradeneeded = () => {
      const db = request.result;
      for (const store of STORES) {
        if (!db.objectStoreNames.contains(store)) db.createObjectStore(store);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () =>
      reject(
        new StorageError('unavailable', 'IndexedDB could not be opened in this browser.', {
          cause: request.error,
        }),
      );
    request.onblocked = () =>
      reject(
        new StorageError(
          'interrupted',
          'Another tab holds the database open; close other Capture tabs and retry.',
        ),
      );
  });
}

function requestDone<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

function txDone(tx: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error ?? new Error('transaction aborted'));
  });
}

export class IndexedDbBackend implements StorageBackend {
  readonly name = 'indexeddb' as const;
  private db: Promise<IDBDatabase> | null = null;

  private connection(): Promise<IDBDatabase> {
    if (!this.db) this.db = openDatabase();
    return this.db;
  }

  private async run<T>(mode: IDBTransactionMode, fn: (db: IDBDatabase) => Promise<T>): Promise<T> {
    try {
      const db = await this.connection();
      return await fn(db);
    } catch (error) {
      if (error instanceof StorageError) throw error;
      throw new StorageError('unavailable', 'IndexedDB request failed.', { cause: error });
    }
  }

  async get<T>(store: StoreName, key: string): Promise<T | undefined> {
    return this.run('readonly', async (db) => {
      const tx = db.transaction(store, 'readonly');
      const result = await requestDone(tx.objectStore(store).get(key));
      await txDone(tx);
      return result as T | undefined;
    });
  }

  async put(store: StoreName, key: string, value: unknown): Promise<void> {
    try {
      await this.run('readwrite', async (db) => {
        const tx = db.transaction(store, 'readwrite');
        tx.objectStore(store).put(value, key);
        await txDone(tx);
      });
    } catch (error) {
      if (error instanceof StorageError && error.kind !== 'interrupted') throw error;
      throw classifyWriteError(error instanceof StorageError ? error.cause : error);
    }
  }

  async remove(store: StoreName, key: string): Promise<void> {
    await this.run('readwrite', async (db) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).delete(key);
      await txDone(tx);
    });
  }

  async all<T>(store: StoreName): Promise<Array<{ key: string; value: T }>> {
    return this.run('readonly', async (db) => {
      const tx = db.transaction(store, 'readonly');
      const entries: Array<{ key: string; value: T }> = [];
      const cursorRequest = tx.objectStore(store).openCursor();
      await new Promise<void>((resolve, reject) => {
        cursorRequest.onsuccess = () => {
          const cursor = cursorRequest.result;
          if (!cursor) {
            resolve();
            return;
          }
          entries.push({ key: String(cursor.key), value: cursor.value as T });
          cursor.continue();
        };
        cursorRequest.onerror = () => reject(cursorRequest.error);
      });
      await txDone(tx);
      return entries;
    });
  }

  async clear(store: StoreName): Promise<void> {
    await this.run('readwrite', async (db) => {
      const tx = db.transaction(store, 'readwrite');
      tx.objectStore(store).clear();
      await txDone(tx);
    });
  }

  close(): void {
    if (this.db) {
      void this.db.then(
        (db) => db.close(),
        () => undefined,
      );
      this.db = null;
    }
  }
}

// ---------------------------------------------------------------------------
// Memory backend (tests, no-storage fallback)
// ---------------------------------------------------------------------------

export class MemoryBackend implements StorageBackend {
  readonly name = 'memory' as const;
  private tables = new Map<StoreName, Map<string, unknown>>();

  private table(store: StoreName): Map<string, unknown> {
    let table = this.tables.get(store);
    if (!table) {
      table = new Map();
      this.tables.set(store, table);
    }
    return table;
  }

  async get<T>(store: StoreName, key: string): Promise<T | undefined> {
    return this.table(store).get(key) as T | undefined;
  }

  async put(store: StoreName, key: string, value: unknown): Promise<void> {
    this.table(store).set(key, value);
  }

  async remove(store: StoreName, key: string): Promise<void> {
    this.table(store).delete(key);
  }

  async all<T>(store: StoreName): Promise<Array<{ key: string; value: T }>> {
    return [...this.table(store).entries()].map(([key, value]) => ({ key, value: value as T }));
  }

  async clear(store: StoreName): Promise<void> {
    this.table(store).clear();
  }

  close(): void {
    // Nothing to release.
  }
}

/** Storage usage estimate for the Export screen; null when unmeasurable. */
export async function storageEstimate(): Promise<{ usage: number; quota: number } | null> {
  try {
    if (typeof navigator === 'undefined' || !navigator.storage?.estimate) return null;
    const { usage, quota } = await navigator.storage.estimate();
    if (typeof usage !== 'number' || typeof quota !== 'number') return null;
    return { usage, quota };
  } catch {
    return null;
  }
}
