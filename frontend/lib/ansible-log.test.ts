import { describe, expect, it } from "vitest"
import { FAILURE_KINDS, parseAnsibleLog } from "./ansible-log"

const LOG = [
  "=== Ansible output ===",
  "[preflight] host reachable",
  "",
  "PLAY [all] *********************************************************************",
  "",
  "TASK [Gathering Facts] *********************************************************",
  "ok: [web1]",
  "fatal: [db1]: UNREACHABLE! => {",
  '    "changed": false',
  "}",
  "",
  "TASK [apt upgrade] *************************************************************",
  "changed: [web1]",
  'failed: [web1] (item=nginx) => {"msg": "boom"}',
  "...ignoring",
  "[WARNING]: something odd",
  "more about it",
  "",
  "[ERROR]: Task failed",
  "  explanation",
  "",
  "PLAY RECAP *********************************************************************",
  "web1                       : ok=2    changed=1    unreachable=0    failed=1",
  "db1                        : ok=0    changed=0    unreachable=1    failed=0",
  "[cleanup] status=failed",
].join("\n")

describe("parseAnsibleLog", () => {
  const lines = parseAnsibleLog(LOG)
  const kindOf = (text: string) => lines.find((l) => l.text === text)?.kind

  it("keeps every line's exact text", () => {
    expect(lines.map((l) => l.text).join("\n")).toBe(LOG)
  })

  it("classifies structure and results", () => {
    expect(kindOf("=== Ansible output ===")).toBe("section")
    expect(kindOf("[preflight] host reachable")).toBe("unbork")
    expect(kindOf("TASK [apt upgrade] *************************************************************")).toBe("task")
    expect(kindOf("ok: [web1]")).toBe("ok")
    expect(kindOf("changed: [web1]")).toBe("changed")
    expect(kindOf('failed: [web1] (item=nginx) => {"msg": "boom"}')).toBe("failed")
    expect(kindOf("...ignoring")).toBe("ignored")
    expect(kindOf("[cleanup] status=failed")).toBe("unbork-failed")
  })

  it("reads 'fatal: ... UNREACHABLE!' as fatal and folds its JSON block into it", () => {
    const at = lines.findIndex((l) => l.text.startsWith("fatal: [db1]"))
    expect(lines[at].kind).toBe("fatal")
    expect(lines[at + 1]).toEqual({ text: '    "changed": false', kind: "fatal", cont: true })
    expect(lines[at + 2]).toEqual({ text: "}", kind: "fatal", cont: true })
  })

  it("continues a warning until a blank line and an error until the next structure", () => {
    expect(kindOf("[WARNING]: something odd")).toBe("warning")
    expect(lines.find((l) => l.text === "more about it")).toMatchObject({ kind: "warning", cont: true })
    expect(kindOf("[ERROR]: Task failed")).toBe("error")
    expect(lines.find((l) => l.text === "  explanation")).toMatchObject({ kind: "error", cont: true })
  })

  it("marks the recap header and its rows", () => {
    expect(kindOf("PLAY RECAP *********************************************************************")).toBe("recap-head")
    expect(lines.filter((l) => l.kind === "recap-row")).toHaveLength(2)
  })

  it("counts each failure once in FAILURE_KINDS", () => {
    const stops = lines.filter((l) => !l.cont && FAILURE_KINDS.has(l.kind)).map((l) => l.kind)
    expect(stops).toEqual(["fatal", "failed", "error", "unbork-failed"])
  })
})
