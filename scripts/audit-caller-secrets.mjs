#!/usr/bin/env node
/**
 * audit-caller-secrets.mjs — fleet-wide provisioning audit for the reusable
 * release templates hosted in this repo.
 *
 * WHY THIS EXISTS
 * ---------------
 * Every platform repo calls `Tanvrit/artifactory/.github/workflows/release-*-template.yml`
 * with `secrets: inherit`. `inherit` only forwards secrets the CALLER repo actually
 * defines — a caller that is missing ARTIFACTS_REPO_TOKEN / R2_* / CF_ACCOUNT_ID does
 * not fail loudly, it releases and silently uploads nothing. Organisation-level secrets
 * are NOT injected into private repos on the GitHub Free plan, so every caller must be
 * provisioned at the repo level, one by one. This script makes that gap visible on a
 * schedule instead of at release time.
 *
 * TOKEN SCOPE NOTE (important)
 * ----------------------------
 * `GET /orgs/{org}/actions/secrets` and other `/orgs/...` endpoints return 403 without
 * the `admin:org` scope, which the default GITHUB_TOKEN and the standard PAT do not
 * carry. Everything here therefore uses only PER-REPO / PER-USER endpoints:
 *   GET /user/repos                              (repo enumeration)
 *   GET /repos/{o}/{r}/contents/...              (caller detection)
 *   GET /repos/{o}/{r}/actions/secrets           (provisioning state; needs repo admin)
 * Consequence: repos the token cannot see are invisible to this audit, and repos where
 * the token is not an admin are reported as UNKNOWN rather than silently passing.
 *
 * The caller list is DERIVED, never hardcoded: we crawl `.github/workflows` of every
 * visible repo and look for a `uses:` pointing at this repo's templates. The required
 * secret set per template is likewise derived by grepping the template sources in this
 * working tree, so it cannot drift away from what the templates actually consume.
 *
 * Usage:
 *   GH_TOKEN=$(gh auth token) node scripts/audit-caller-secrets.mjs [--json out.json]
 * Env:
 *   GH_TOKEN / GITHUB_TOKEN   required
 *   AUDIT_ORG                 default "Tanvrit"
 *
 * Exit codes — kept distinct so a red run is never ambiguous:
 *   0  every caller is fully provisioned
 *   1  at least one caller is missing a required secret (the finding this exists for)
 *   2  unexpected error (bug / GitHub outage)
 *   3  the token cannot do the job (expired, wrong type, or missing scope) — the audit
 *      did NOT run. Distinct from 1 so a dead token never masquerades as a fleet gap.
 */

import { readdir, readFile, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.resolve(HERE, '..');
const WORKFLOW_DIR = path.join(REPO_ROOT, '.github', 'workflows');

const ORG = process.env.AUDIT_ORG || 'Tanvrit';
const TOKEN = process.env.GH_TOKEN || process.env.GITHUB_TOKEN;
const API = 'https://api.github.com';

/**
 * The secrets a caller repo must provision itself for `secrets: inherit` to deliver
 * anything useful. These are the shared-infrastructure credentials (artifact upload +
 * R2 mirror); per-app signing material is deliberately NOT audited here because it is
 * legitimately absent on repos that do not ship that OS.
 */
const CORE_REQUIREMENTS = [
  { name: 'ARTIFACTS_REPO_TOKEN', alternatives: [] },
  { name: 'R2_ACCESS_KEY_ID', alternatives: [] },
  { name: 'R2_SECRET_ACCESS_KEY', alternatives: [] },
  // The R2 mirror step resolves `acct="${CF_ACCOUNT_ID:-${CLOUDFLARE_ACCOUNT_ID:-}}"`, so
  // either name satisfies it. Auditing CF_ACCOUNT_ID alone would report a false gap on
  // every repo that already carries CLOUDFLARE_ACCOUNT_ID — and a checker that cries wolf
  // gets ignored, which is the failure mode this whole workflow exists to prevent.
  { name: 'CF_ACCOUNT_ID', alternatives: ['CLOUDFLARE_ACCOUNT_ID'] },
];
const CORE_SECRETS = CORE_REQUIREMENTS.map((r) => r.name);

/** Every name that can satisfy a requirement. */
function acceptedNames(req) {
  return [req.name, ...req.alternatives];
}

if (!TOKEN && !process.argv.includes('--self-test')) {
  console.error('FATAL: GH_TOKEN (or GITHUB_TOKEN) is not set.');
  process.exit(2);
}

// ---------------------------------------------------------------- http helpers

let apiCalls = 0;

async function gh(pathOrUrl, { raw = false, allow404 = false, allowForbidden = false } = {}) {
  const url = pathOrUrl.startsWith('http') ? pathOrUrl : `${API}${pathOrUrl}`;
  const headers = {
    Authorization: `Bearer ${TOKEN}`,
    Accept: raw ? 'application/vnd.github.raw' : 'application/vnd.github+json',
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'tanvrit-artifactory-secret-audit',
  };
  for (let attempt = 0; attempt < 4; attempt++) {
    apiCalls++;
    const res = await fetch(url, { headers });
    if (res.status === 404 && allow404) return { status: 404, body: null, res };
    if ((res.status === 403 || res.status === 401) && allowForbidden) {
      return { status: res.status, body: null, res };
    }
    if (res.status === 403 && res.headers.get('x-ratelimit-remaining') === '0') {
      const reset = Number(res.headers.get('x-ratelimit-reset') || 0) * 1000;
      const wait = Math.max(1000, Math.min(60000, reset - Date.now()));
      console.error(`  rate-limited, sleeping ${Math.round(wait / 1000)}s`);
      await new Promise((r) => setTimeout(r, wait));
      continue;
    }
    if (res.status >= 500 || res.status === 429) {
      await new Promise((r) => setTimeout(r, 500 * 2 ** attempt));
      continue;
    }
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText} for ${url}: ${(await res.text()).slice(0, 300)}`);
    }
    return { status: res.status, body: raw ? await res.text() : await res.json(), res };
  }
  throw new Error(`giving up after retries: ${url}`);
}

async function ghPaged(pathname) {
  const out = [];
  let url = `${API}${pathname}${pathname.includes('?') ? '&' : '?'}per_page=100`;
  while (url) {
    const { body, res } = await gh(url);
    out.push(...body);
    const link = res.headers.get('link') || '';
    const next = link.split(',').find((p) => p.includes('rel="next"'));
    url = next ? next.slice(next.indexOf('<') + 1, next.indexOf('>')) : null;
  }
  return out;
}

// ------------------------------------------------- step 1: template requirements

/** Derive, per template file, which CORE secrets it actually references. */
async function readTemplateRequirements() {
  const files = (await readdir(WORKFLOW_DIR)).filter(
    (f) => /^release-.*-template\.ya?ml$/.test(f),
  );
  if (files.length === 0) throw new Error(`no release templates found in ${WORKFLOW_DIR}`);
  const perTemplate = {};
  const allReferenced = new Set();
  for (const f of files) {
    const src = await readFile(path.join(WORKFLOW_DIR, f), 'utf8');
    const referenced = new Set(
      [...src.matchAll(/secrets\.([A-Z0-9_]+)/g)].map((m) => m[1]),
    );
    for (const s of referenced) allReferenced.add(s);
    // A requirement applies to a template if the template consumes ANY name that can
    // satisfy it — so renaming CF_ACCOUNT_ID -> CLOUDFLARE_ACCOUNT_ID upstream does not
    // silently drop the requirement from the audit.
    perTemplate[f] = CORE_REQUIREMENTS.filter((r) =>
      acceptedNames(r).some((n) => referenced.has(n)),
    ).map((r) => r.name);
  }
  return { perTemplate, allReferenced: [...allReferenced].sort() };
}

// --------------------------------------------------- step 2: derive caller repos

const USES_RE = new RegExp(
  `${ORG}/artifactory/\\.github/workflows/(release-[a-z0-9-]+-template\\.ya?ml)@([A-Za-z0-9._/-]+)`,
  'gi',
);

/**
 * Drop full-line YAML comments before matching. The templates themselves carry a
 * commented-out `uses:` line in their header docs, and so do some callers; a commented
 * reference is documentation, not a call. Without this the template repo reports itself
 * as its own caller.
 */
function stripComments(src) {
  return src
    .split('\n')
    .filter((line) => !/^\s*#/.test(line))
    .join('\n');
}

async function findCallers(repos, perTemplate) {
  const callers = [];
  for (const repo of repos) {
    const dir = await gh(
      `/repos/${repo.full_name}/contents/.github/workflows`,
      { allow404: true, allowForbidden: true },
    );
    if (!dir.body || !Array.isArray(dir.body)) continue;
    const yml = dir.body.filter(
      (e) => e.type === 'file' && /\.ya?ml$/i.test(e.name),
    );
    const templates = new Map(); // templateFile -> Set(ref)
    const workflowFiles = [];
    for (const entry of yml) {
      const file = await gh(
        `/repos/${repo.full_name}/contents/${encodeURI(entry.path)}`,
        { raw: true, allow404: true, allowForbidden: true },
      );
      if (typeof file.body !== 'string') continue;
      let hit = false;
      for (const m of stripComments(file.body).matchAll(USES_RE)) {
        hit = true;
        const tpl = m[1].toLowerCase();
        if (!templates.has(tpl)) templates.set(tpl, new Set());
        templates.get(tpl).add(m[2]);
      }
      if (hit) workflowFiles.push(entry.name);
    }
    if (templates.size === 0) continue;

    const unknownTemplates = [...templates.keys()].filter((t) => !(t in perTemplate));
    const required = new Set();
    for (const [tpl] of templates) {
      for (const s of perTemplate[tpl] || CORE_SECRETS) required.add(s);
    }
    callers.push({
      repo: repo.full_name,
      name: repo.name,
      private: repo.private,
      archived: repo.archived,
      templates: [...templates.keys()].sort(),
      refs: [...new Set([...templates.values()].flatMap((s) => [...s]))].sort(),
      unknownTemplates,
      workflowFiles: workflowFiles.sort(),
      required: CORE_SECRETS.filter((s) => required.has(s)),
    });
    process.stderr.write(`  caller: ${repo.full_name} (${templates.size} templates)\n`);
  }
  return callers;
}

// ------------------------------------------------- step 3: per-repo secret state

async function readSecrets(caller) {
  const r = await gh(`/repos/${caller.repo}/actions/secrets`, {
    allow404: true,
    allowForbidden: true,
  });
  if (!r.body) {
    return { readable: false, names: [], reason: `HTTP ${r.status} (needs repo admin)` };
  }
  return { readable: true, names: r.body.secrets.map((s) => s.name), reason: null };
}

// -------------------------------------------------------------------- reporting

function esc(s) {
  return String(s).replace(/\|/g, '\\|');
}

function buildReport(results, meta) {
  const L = [];
  const missingRepos = results.filter((r) => r.readable && r.missing.length > 0);
  const unknownRepos = results.filter((r) => !r.readable);
  const okRepos = results.filter((r) => r.readable && r.missing.length === 0);

  L.push('# Release-template caller secret audit');
  L.push('');
  L.push(
    `**${results.length}** repos call this repo's release templates. ` +
      `**${okRepos.length}** fully provisioned · **${missingRepos.length}** missing secrets · ` +
      `**${unknownRepos.length}** not readable.`,
  );
  L.push('');
  L.push(
    '> Callers use `secrets: inherit`, which forwards only secrets the **caller repo** defines. ' +
      'Org-level secrets are not injected into private repos on the GitHub Free plan, so each ' +
      'caller must carry its own copy. A missing secret does not fail a release — it ships nothing.',
  );
  L.push('');

  // --- matrix table
  L.push('## Provisioning matrix');
  L.push('');
  L.push(`| Repo | ${CORE_SECRETS.join(' | ')} | Status |`);
  L.push(`|---|${CORE_SECRETS.map(() => ':-:').join('|')}|---|`);
  for (const r of results) {
    const cells = CORE_REQUIREMENTS.map((req) => {
      if (!r.required.includes(req.name)) return '–';
      if (!r.readable) return '?';
      if (r.names.includes(req.name)) return '✅';
      const alt = req.alternatives.find((a) => r.names.includes(a));
      return alt ? `✅<sub>${alt}</sub>` : '❌';
    });
    const status = !r.readable
      ? `⚠️ unreadable (${r.reason})`
      : r.missing.length === 0
        ? '✅ ok'
        : `❌ missing ${r.missing.length}`;
    L.push(`| \`${esc(r.repo)}\` | ${cells.join(' | ')} | ${status} |`);
  }
  L.push('');
  L.push(
    'Legend: ✅ present · ✅<sub>NAME</sub> satisfied by an accepted alternative name · ' +
      '❌ **missing** · – not required by the templates this repo calls · ? not readable.',
  );
  L.push('');

  // --- fix commands
  if (missingRepos.length > 0) {
    L.push('## Fix — paste these');
    L.push('');
    L.push('Run locally with a `gh` login that is an admin on each repo. Values come from your');
    L.push('password manager / Cloudflare dashboard; they are never printed by this audit.');
    L.push('');
    L.push('```bash');
    L.push('# export the four values once, then run the block below');
    for (const s of CORE_SECRETS) L.push(`export ${s}='…'`);
    L.push('');
    for (const r of missingRepos) {
      L.push(`# ${r.repo} — missing: ${r.missing.join(', ')}`);
      for (const s of r.missing) {
        L.push(`printf '%s' "$${s}" | gh secret set ${s} --repo ${r.repo}`);
      }
    }
    L.push('```');
    L.push('');
  }

  if (unknownRepos.length > 0) {
    L.push('## Not readable');
    L.push('');
    L.push('These callers were found, but `GET /repos/…/actions/secrets` was refused — the token');
    L.push('is not an admin there. They are counted as **unknown**, never as provisioned.');
    L.push('');
    for (const r of unknownRepos) L.push(`- \`${r.repo}\` — ${r.reason}`);
    L.push('');
  }

  // --- drift guards
  L.push('## Derivation & drift');
  L.push('');
  L.push(`- Caller list is **derived**, not hardcoded: crawled \`.github/workflows\` of ${meta.reposScanned} repos visible to this token in \`${ORG}\`.`);
  L.push(`- Required-secret set per template is derived from the template sources in this repo (${Object.keys(meta.perTemplate).length} templates).`);
  L.push(`- Templates referenced by callers at refs: ${meta.refs.map((r) => `\`${r}\``).join(', ') || 'n/a'}.`);
  const strays = results.flatMap((r) => r.unknownTemplates.map((t) => `${r.repo} → ${t}`));
  if (strays.length) {
    L.push(`- ⚠️ **Callers reference templates that do not exist in this repo** (renamed or deleted): ${strays.map((s) => `\`${s}\``).join(', ')}`);
  }
  const known = new Set(CORE_REQUIREMENTS.flatMap(acceptedNames));
  const uncovered = meta.allReferenced.filter(
    (s) => !known.has(s) && /^(R2_|CF_|CLOUDFLARE_|ARTIFACTS_)/.test(s),
  );
  if (uncovered.length) {
    L.push(`- ℹ️ Infra-shaped secrets referenced by templates but **not audited** (add to \`CORE_SECRETS\` if they become required): ${uncovered.map((s) => `\`${s}\``).join(', ')}`);
  }
  L.push('- ⚠️ Blind spot: `/orgs/…` endpoints need `admin:org` and return 403 with the standard token, so this audit uses per-repo endpoints only. **Repos this token cannot see are not audited at all.**');
  L.push('');
  L.push(`_Generated ${meta.generatedAt} · ${meta.apiCalls} API calls._`);
  return L.join('\n');
}

// ------------------------------------------------------- probe self-test (--self-test)

/**
 * Positive + negative controls for the detection logic. A crawl that silently matches
 * nothing would otherwise report a clean fleet, which is exactly the failure mode this
 * workflow exists to prevent. Runs offline; no token needed.
 */
async function selfTest() {
  const fails = [];
  const positive = `
  release:
    uses: ${ORG}/artifactory/.github/workflows/release-macos-template.yml@main
    secrets: inherit
`;
  const negative = `
  # uses: ${ORG}/artifactory/.github/workflows/release-macos-template.yml@main
  #       secrets: inherit
`;
  const hits = [...stripComments(positive).matchAll(USES_RE)];
  if (hits.length !== 1) fails.push(`positive control: expected 1 match, got ${hits.length}`);
  else {
    if (hits[0][1] !== 'release-macos-template.yml') fails.push(`positive control: template captured as "${hits[0][1]}"`);
    if (hits[0][2] !== 'main') fails.push(`positive control: ref captured as "${hits[0][2]}" (expected "main")`);
  }
  const negHits = [...stripComments(negative).matchAll(USES_RE)];
  if (negHits.length !== 0) fails.push(`negative control: commented uses: matched ${negHits.length} time(s)`);

  const { perTemplate, allReferenced } = await readTemplateRequirements();
  const n = Object.keys(perTemplate).length;
  if (n === 0) fails.push('template scan: found 0 release templates');
  // A declared alternative that no template consumes any more is stale: it would make the
  // audit accept a secret the release actually ignores — a false green.
  for (const req of CORE_REQUIREMENTS) {
    for (const alt of req.alternatives) {
      if (!allReferenced.includes(alt)) {
        fails.push(
          `stale alternative: ${alt} is accepted for ${req.name} but no template references it any more`,
        );
      }
    }
  }
  if (!Object.values(perTemplate).some((v) => v.includes('ARTIFACTS_REPO_TOKEN'))) {
    fails.push('template scan: no template requires ARTIFACTS_REPO_TOKEN — requirement derivation is broken');
  }
  for (const f of fails) console.error(`SELF-TEST FAIL: ${f}`);
  console.log(
    fails.length === 0
      ? `self-test OK (${n} templates, positive+negative controls pass)`
      : `self-test FAILED (${fails.length})`,
  );
  return fails.length === 0 ? 0 : 2;
}

// ------------------------------------------------------------------------ main

class CredentialError extends Error {}

/**
 * Preflight the token before crawling. Two failure modes are common and produce very
 * different fixes, so they must not both surface as a generic stack trace:
 *   * 401 — the PAT is expired or revoked.
 *   * 403 "Resource not accessible by integration" — GITHUB_TOKEN was supplied. The
 *     Actions token is an installation token: it cannot call /user/repos at all, and it
 *     is scoped to a single repo, so it can never audit the fleet.
 */
async function preflight() {
  const r = await gh('/user', { allowForbidden: true, allow404: true });
  if (r.status === 401) {
    throw new CredentialError(
      'GH_TOKEN was rejected (401 Bad credentials) — the token is expired or revoked. ' +
        'In CI this usually means the secret it came from needs rotating.',
    );
  }
  if (r.status === 403 || !r.body || !r.body.login) {
    throw new CredentialError(
      'GH_TOKEN cannot call GET /user (403). This is what the built-in GITHUB_TOKEN does: ' +
        'it is an installation token scoped to one repo and cannot enumerate the fleet. ' +
        'Provide a user PAT with `repo` scope as the AUDIT_FLEET_TOKEN secret.',
    );
  }
  return r.body.login;
}

async function main() {
  if (process.argv.includes('--self-test')) return selfTest();

  const login = await preflight();
  process.stderr.write(`authenticated as ${login}\n`);

  const jsonOutIdx = process.argv.indexOf('--json');
  const jsonOut = jsonOutIdx > -1 ? process.argv[jsonOutIdx + 1] : null;

  const { perTemplate, allReferenced } = await readTemplateRequirements();
  process.stderr.write(
    `templates: ${Object.entries(perTemplate)
      .map(([k, v]) => `${k.replace(/^release-|-template\.ya?ml$/g, '')}[${v.length}]`)
      .join(' ')}\n`,
  );

  const allRepos = await ghPaged(
    '/user/repos?affiliation=owner,collaborator,organization_member&sort=full_name',
  );
  const repos = allRepos.filter(
    (r) => r.owner.login.toLowerCase() === ORG.toLowerCase() && !r.archived,
  );
  process.stderr.write(`scanning ${repos.length} non-archived ${ORG} repos…\n`);

  const callers = await findCallers(repos, perTemplate);
  callers.sort((a, b) => a.repo.localeCompare(b.repo));

  const results = [];
  for (const c of callers) {
    const s = await readSecrets(c);
    const missing = s.readable
      ? CORE_REQUIREMENTS.filter(
          (req) =>
            c.required.includes(req.name) &&
            !acceptedNames(req).some((n) => s.names.includes(n)),
        ).map((req) => req.name)
      : [];
    results.push({ ...c, ...s, missing });
  }

  const meta = {
    reposScanned: repos.length,
    perTemplate,
    allReferenced,
    refs: [...new Set(callers.flatMap((c) => c.refs))].sort(),
    generatedAt: new Date().toISOString(),
    apiCalls,
  };
  const report = buildReport(results, meta);

  const summaryFile = process.env.GITHUB_STEP_SUMMARY;
  if (summaryFile) await writeFile(summaryFile, report + '\n', { flag: 'a' });
  console.log(report);

  if (jsonOut) await writeFile(jsonOut, JSON.stringify({ meta, results }, null, 2));

  const unprovisioned = results.filter((r) => r.readable && r.missing.length > 0);
  const unreadable = results.filter((r) => !r.readable);
  for (const r of unprovisioned) {
    console.log(`::error title=Unprovisioned release caller::${r.repo} is missing ${r.missing.join(', ')} — its releases will silently upload nothing.`);
  }
  for (const r of unreadable) {
    console.log(`::warning title=Caller not auditable::${r.repo} — ${r.reason}`);
  }
  if (results.length === 0) {
    console.log('::error title=Audit found no callers::Enumeration returned zero callers — the probe is broken or the token lost visibility. Treating as failure.');
    return 1;
  }
  return unprovisioned.length > 0 ? 1 : 0;
}

main().then(
  (code) => process.exit(code),
  (err) => {
    if (err instanceof CredentialError) {
      console.error(`CREDENTIAL PROBLEM — the audit did not run:\n  ${err.message}`);
      console.log(`::error title=Secret audit could not run::${err.message}`);
      process.exit(3);
    }
    console.error(err);
    process.exit(2);
  },
);
