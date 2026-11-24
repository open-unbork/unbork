import { test, expect } from "./fixtures"

// S-008: the two provider types that run the Claude Code program are
// labelled as our integration ("Claude agent", "Claude single-shot"), not
// under the program's own name.
test.describe("AI provider type labels", () => {
  test("no type is offered as Claude Code", async ({ page }) => {
    await page.goto("/ai-providers")
    await page.getByRole("button", { name: "Add provider…" }).click()
    const options = page.locator("#provider_type option")
    await expect(options.filter({ hasText: "Claude agent" })).toHaveCount(1)
    await expect(options.filter({ hasText: "Claude single-shot" })).toHaveCount(1)
    await expect(options.filter({ hasText: "Claude Code" })).toHaveCount(0)
  })
})
