import { indexedDB, IDBKeyRange } from 'fake-indexeddb'

Object.assign(globalThis, { indexedDB, IDBKeyRange })
