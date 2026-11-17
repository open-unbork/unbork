// List the npm packages the production build ships, with their licences
// (S-010). Reads package-lock.json and each package's own package.json and
// licence files from node_modules, so it needs nothing installed beyond
// `npm ci`. Development-only packages (`dev` or `devOptional` in the lock)
// are left out: they build the pages but do not ship in them.
//
//   node scripts/licences.mjs > frontend-licences.json
//
// backend/scripts/third_party_notices.py reads the output.

import { existsSync, readdirSync, readFileSync } from "node:fs"
import { join } from "node:path"

const root = new URL("..", import.meta.url).pathname
const lock = JSON.parse(readFileSync(join(root, "package-lock.json"), "utf8"))
const TEXT = /^(licen[cs]e|copying|notice)/i

function licenceOf(pkg) {
  if (typeof pkg.license === "string") return pkg.license
  if (pkg.license && typeof pkg.license.type === "string") return pkg.license.type
  if (Array.isArray(pkg.licenses)) return pkg.licenses.map((l) => l.type ?? l).join(" OR ")
  return ""
}

const rows = []
const seen = new Set()
for (const [path, entry] of Object.entries(lock.packages ?? {})) {
  if (!path.startsWith("node_modules/") || entry.dev || entry.devOptional || entry.link) continue
  const dir = join(root, path)
  if (!existsSync(join(dir, "package.json"))) continue // optional, not installed here
  const pkg = JSON.parse(readFileSync(join(dir, "package.json"), "utf8"))
  const key = `${pkg.name}@${pkg.version}`
  if (seen.has(key)) continue
  seen.add(key)
  const texts = readdirSync(dir)
    .filter((f) => TEXT.test(f))
    .sort()
    .map((f) => readFileSync(join(dir, f), "utf8"))
  rows.push({ name: pkg.name, version: pkg.version, license: licenceOf(pkg) || entry.license || "", texts })
}
rows.sort((a, b) => a.name.localeCompare(b.name) || a.version.localeCompare(b.version))
process.stdout.write(JSON.stringify(rows, null, 1) + "\n")
