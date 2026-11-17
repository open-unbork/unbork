import { describe, expect, it } from "vitest"

import { featuresOf } from "./features"
import type { VersionInfo } from "./types"

const base: VersionInfo = {
  version: "2.0.0",
  commit_sha: null,
  commit_sha_short: null,
  build_date: null,
  license: "AGPL-3.0-or-later",
  repo_url: "https://example.invalid",
}

describe("featuresOf", () => {
  it("treats an older server as having the assistant", () => {
    expect(featuresOf(base)).toEqual({ assistant: true })
    expect(featuresOf(undefined)).toEqual({ assistant: true })
  })

  it("reads the server's answer", () => {
    expect(featuresOf({ ...base, features: { assistant: false } })).toEqual({ assistant: false })
  })
})
