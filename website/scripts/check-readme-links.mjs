#!/usr/bin/env node
// Checks that every link from README.md into the documentation site points
// at a page (and an anchor) the site actually has.
//
//   node website/scripts/check-readme-links.mjs          # against website/build
//   node website/scripts/check-readme-links.mjs --live   # against the live site
//
// The default mode needs `npm run build` first. --live fetches each page
// and expects 200, and checks anchors in what came back.
import { existsSync, readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const SITE = 'https://open-unbork.github.io/unbork/';
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..', '..');
const build = join(here, '..', 'build');
const live = process.argv.includes('--live');

const readme = readFileSync(join(root, 'README.md'), 'utf8');
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&');
const escaped = escapeRe(SITE);
const links = [...new Set(readme.match(new RegExp(`${escaped}[^)\\s"'<>]*`, 'g')) ?? [])];

if (links.length === 0) {
  console.error('No links to the documentation site found in README.md');
  process.exit(1);
}
if (!live && !existsSync(join(build, 'index.html'))) {
  console.error(`No site build at ${build}; run \`npm run build\` in website/ first`);
  process.exit(1);
}

const pages = new Map();
async function page(path) {
  if (!pages.has(path)) pages.set(path, load(path));
  return pages.get(path);
}
async function load(path) {
  if (live) {
    try {
      const res = await fetch(SITE + path, { signal: AbortSignal.timeout(15000) });
      return res.ok ? await res.text() : null;
    } catch {
      return null; // timed out or unreachable: reported as not answering 200
    }
  }
  const file = join(build, path, 'index.html');
  return existsSync(file) ? readFileSync(file, 'utf8') : null;
}

// The build is minified, so attributes may be unquoted (id=installation).
function hasId(html, id) {
  const quoted = html.includes(`id="${id}"`) || html.includes(`id='${id}'`);
  return quoted || new RegExp(`\\bid=${escapeRe(id)}(?=[\\s>/])`).test(html);
}

let bad = 0;
for (const link of links) {
  const rest = link.slice(SITE.length);
  const [rawPath, anchor] = rest.split('#');
  const path = rawPath.replace(/^\/+|\/+$/g, '');
  const html = await page(path);
  let problem = null;
  if (html === null) problem = live ? 'page does not answer 200 within 15 s' : `no page ${path || '/'} in the build`;
  else if (anchor && !hasId(html, anchor)) problem = `no anchor #${anchor} on the page`;
  if (problem) {
    bad += 1;
    console.error(`BROKEN  ${link}  (${problem})`);
  } else {
    console.log(`ok      ${link}`);
  }
}

if (bad) {
  console.error(`\n${bad} of ${links.length} README links to the site are broken.`);
  process.exit(1);
}
console.log(`\nAll ${links.length} README links to the site resolve${live ? ' on the live site' : ''}.`);
