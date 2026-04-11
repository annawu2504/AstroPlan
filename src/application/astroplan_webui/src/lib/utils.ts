import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'
import type { StoreApi, UseBoundStore } from 'zustand'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/** createSelectors — same pattern as LightRAG. Adds .use.field() selectors. */
type WithSelectors<S> = S extends { getState: () => infer T }
  ? S & { use: { [K in keyof T]: () => T[K] } }
  : never

export function createSelectors<S extends UseBoundStore<StoreApi<object>>>(
  _store: S,
): WithSelectors<typeof _store> {
  const store = _store as WithSelectors<typeof _store>
  store.use = {} as WithSelectors<typeof _store>['use']
  for (const k of Object.keys(store.getState())) {
    ;(store.use as Record<string, unknown>)[k] = () =>
      store((s: Record<string, unknown>) => s[k])
  }
  return store
}

/** Format unix epoch ms as a locale string. */
export function formatTime(ts: number): string {
  return new Date(ts).toLocaleString('zh-CN', {
    hour12: false,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  })
}
