import { act, render, screen } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { CronInput } from "./cron-input"

const apiFetch = vi.hoisted(() => vi.fn())
vi.mock("@/lib/api", () => ({ apiFetch }))
vi.mock("@/lib/schedule-timezone", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/lib/schedule-timezone")>()),
  useScheduleTimezone: () => "UTC",
}))

const VALID = { valid: true, message: null, next_run_at: ["2026-11-16T03:00:00Z"], timezone: "UTC" }

/** Let the 250 ms debounce fire and the mocked request settle. */
async function settle() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(250)
  })
}

describe("CronInput", () => {
  beforeEach(() => {
    vi.useFakeTimers()
    apiFetch.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it("asks the server once after the debounce and shows the next runs", async () => {
    apiFetch.mockResolvedValue(VALID)
    render(<CronInput value="0 3 * * *" onChange={() => {}} />)

    expect(screen.getByText("checking…")).toBeTruthy()
    expect(apiFetch).not.toHaveBeenCalled()

    await settle()

    expect(apiFetch).toHaveBeenCalledTimes(1)
    expect(apiFetch).toHaveBeenCalledWith("/api/scheduled-actions/validate-cron", { method: "POST", json: { cron: "0 3 * * *" } })
    expect(screen.getByText("next runs (UTC):")).toBeTruthy()
    expect(screen.queryByText("checking…")).toBeNull()
  })

  it("makes no request for an empty value", async () => {
    render(<CronInput value="" onChange={() => {}} />)
    await settle()
    expect(apiFetch).not.toHaveBeenCalled()
    expect(screen.queryByText("checking…")).toBeNull()
  })

  it("shows the server's message for an invalid expression", async () => {
    apiFetch.mockResolvedValue({ valid: false, message: "bad minute field", next_run_at: [], timezone: "UTC" })
    render(<CronInput value="99 * * * *" onChange={() => {}} />)
    await settle()
    expect(screen.getByRole("alert").textContent).toContain("bad minute field")
  })

  it("says validation failed when the request fails", async () => {
    apiFetch.mockRejectedValue(new Error("network"))
    render(<CronInput value="0 3 * * *" onChange={() => {}} />)
    await settle()
    expect(screen.getByRole("alert").textContent).toContain("Validation failed")
  })

  it("drops the old result when the value changes, and clears it when emptied", async () => {
    apiFetch.mockResolvedValue(VALID)
    const { rerender } = render(<CronInput value="0 3 * * *" onChange={() => {}} />)
    await settle()
    expect(screen.getByText("next runs (UTC):")).toBeTruthy()

    rerender(<CronInput value="0 4 * * *" onChange={() => {}} />)
    expect(screen.queryByText("next runs (UTC):")).toBeNull()
    expect(screen.getByText("checking…")).toBeTruthy()

    rerender(<CronInput value="" onChange={() => {}} />)
    expect(screen.queryByText("checking…")).toBeNull()
    expect(screen.queryByText("next runs (UTC):")).toBeNull()
  })
})
