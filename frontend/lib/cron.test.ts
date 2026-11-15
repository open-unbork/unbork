import { describe, expect, it } from "vitest"
import { cronToHuman } from "./cron"

describe("cronToHuman", () => {
  it.each([
    ["* * * * *", "Every minute"],
    ["0 * * * *", "Every hour"],
    ["0 0 * * *", "Every day at midnight"],
    ["0 3 * * *", "Every day at 3:00"],
    ["*/15 * * * *", "Every 15 minutes"],
    ["30 * * * *", "Every hour at :30"],
    ["5 * * * *", "Every hour at :05"],
    ["0 */6 * * *", "Every 6 hours"],
    ["15 */6 * * *", "Every 6 hours at :15"],
    ["30 4 * * *", "Every day at 4:30"],
    ["0 2 * * 0", "Sundays at 2:00"],
    ["0 2 * * 1-5", "Weekdays at 2:00"],
    ["0 2 * * 6-7", "Sat–Sun at 2:00"],
    ["0 2 * * 1,3,5", "Mon, Wed, Fri at 2:00"],
    ["0 4 1 * *", "Monthly on day 1 at 4:00"],
  ])("%s reads %s", (cron, words) => {
    expect(cronToHuman(cron)).toBe(words)
  })

  it("trims surrounding whitespace", () => {
    expect(cronToHuman("  0 3 * * *  ")).toBe("Every day at 3:00")
  })

  it.each([
    "0 0 3 * * *", // six fields
    "0 3 * 6 *", // a month other than *
    "0 2 * * MON", // a weekday name
    "0 2 1 * 1", // both day of month and weekday
  ])("hands %s back unchanged", (cron) => {
    expect(cronToHuman(cron)).toBe(cron)
  })
})
