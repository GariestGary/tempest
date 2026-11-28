/**
 * Vitest global setup.
 * - `@testing-library/jest-dom` adds DOM matchers (toBeInTheDocument, etc.)
 * - window.matchMedia stub: some Tailwind-driven components read it on mount
 *   and jsdom doesn't implement it natively.
 */
import '@testing-library/jest-dom/vitest'

// Node 26 exposes an unset global localStorage that can shadow jsdom's store.
if (!globalThis.localStorage) {
  const values = new Map<string, string>()
  const storage: Storage = {
    get length() { return values.size },
    clear: () => values.clear(),
    getItem: key => values.get(key) ?? null,
    key: index => Array.from(values.keys())[index] ?? null,
    removeItem: key => { values.delete(key) },
    setItem: (key, value) => { values.set(key, value) },
  }
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
}

if (!window.matchMedia) {
  window.matchMedia = (query: string) =>
    ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }) as unknown as MediaQueryList
}

// jsdom 22+ adds a `Clipboard` class on navigator.clipboard, but older
// jsdom builds don't. Provide a minimal stub if absent so components
// calling `navigator.clipboard.writeText(...)` don't throw on mount and
// tests that spy on it have something to spy on.
if (!navigator.clipboard) {
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: async (_: string) => {} },
    writable: true,
    configurable: true,
  })
}
