import { cleanup } from "@testing-library/react"
import { afterEach } from "vitest"

// Unmount whatever a test rendered; with `globals: false` Testing Library
// cannot register this itself.
afterEach(() => {
  cleanup()
})
