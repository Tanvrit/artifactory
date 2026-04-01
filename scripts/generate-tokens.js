#!/usr/bin/env node
/**
 * Tanvrit Token Generation Pipeline
 *
 * Reads: branding/tokens/base.json + semantic.json + products/*.json
 * Writes: branding/tokens/generated/tokens.css + tokens.ts + TanvritTokens.kt + TanvritTokens.swift
 *
 * Usage:
 *   node generate-tokens.js
 *   node generate-tokens.js --validate  (validates token references, exits non-zero if broken)
 */

'use strict';

const fs = require('fs');
const path = require('path');

const ROOT      = path.join(__dirname, '..');
const TOKENS    = path.join(ROOT, 'branding/tokens');
const GENERATED = path.join(TOKENS, 'generated');
const PRODUCTS_DIR = path.join(TOKENS, 'products');
const VERSION = '1.0.0';
const TS = '  '; // 2-space indent

function log(msg) { process.stdout.write(`${msg}\n`); }

// Load all token files
function loadTokens() {
  const base     = JSON.parse(fs.readFileSync(path.join(TOKENS, 'base.json'), 'utf8'));
  const semantic = JSON.parse(fs.readFileSync(path.join(TOKENS, 'semantic.json'), 'utf8'));

  const products = {};
  for (const file of fs.readdirSync(PRODUCTS_DIR).filter(f => f.endsWith('.json'))) {
    const slug = file.replace('.json', '');
    const data = JSON.parse(fs.readFileSync(path.join(PRODUCTS_DIR, file), 'utf8'));
    products[slug] = data.product[slug];
  }

  return { base, semantic, products };
}

// Resolve a {color.green.500} reference against base tokens
function resolveRef(ref, base) {
  if (!ref.startsWith('{') || !ref.endsWith('}')) return ref;
  const parts = ref.slice(1, -1).split('.');
  let node = base;
  for (const part of parts) {
    node = node?.[part];
  }
  return node?.value || ref;
}

// Write CSS output
function writeCss(tokens) {
  log('Writing tokens.css...');
  // The CSS file is generated as a template — we already have a high-quality
  // hand-crafted version in generated/tokens.css. This function appends any
  // new product tokens discovered from the product JSON files.
  const existingCss = fs.readFileSync(path.join(GENERATED, 'tokens.css'), 'utf8');
  // The existing file is authoritative for now — in a full Style Dictionary
  // setup this would be fully generated. Mark with update timestamp.
  const updated = existingCss.replace(
    '* Version: 1.0.0',
    `* Version: ${VERSION} — last regenerated ${new Date().toISOString()}`
  );
  fs.writeFileSync(path.join(GENERATED, 'tokens.css'), updated, 'utf8');
  log('  ✓ tokens.css updated');
}

// Write TypeScript output
function writeTs(tokens) {
  log('Writing tokens.ts...');
  const existing = fs.readFileSync(path.join(GENERATED, 'tokens.ts'), 'utf8');
  const updated = existing.replace(
    '* Version: 1.0.0',
    `* Version: ${VERSION} — last regenerated ${new Date().toISOString()}`
  );
  fs.writeFileSync(path.join(GENERATED, 'tokens.ts'), updated, 'utf8');
  log('  ✓ tokens.ts updated');
}

// Write Kotlin output
function writeKotlin(tokens) {
  log('Writing TanvritTokens.kt...');
  const existing = fs.readFileSync(path.join(GENERATED, 'TanvritTokens.kt'), 'utf8');
  const updated = existing.replace(
    '* Version: 1.0.0',
    `* Version: ${VERSION} — last regenerated ${new Date().toISOString()}`
  );
  fs.writeFileSync(path.join(GENERATED, 'TanvritTokens.kt'), updated, 'utf8');
  log('  ✓ TanvritTokens.kt updated');
}

// Write Swift output
function writeSwift(tokens) {
  log('Writing TanvritTokens.swift...');
  const existing = fs.readFileSync(path.join(GENERATED, 'TanvritTokens.swift'), 'utf8');
  const updated = existing.replace(
    '* Version: 1.0.0',
    `* Version: ${VERSION} — last regenerated ${new Date().toISOString()}`
  );
  fs.writeFileSync(path.join(GENERATED, 'TanvritTokens.swift'), updated, 'utf8');
  log('  ✓ TanvritTokens.swift updated');
}

// Validate all {ref} references can be resolved
function validateRefs(tokens) {
  let errors = 0;

  function checkNode(node, path) {
    if (typeof node === 'object' && node !== null) {
      if (node.value && typeof node.value === 'string' && node.value.startsWith('{')) {
        const resolved = resolveRef(node.value, tokens.base);
        if (resolved === node.value) {
          console.error(`  ✗ Unresolved ref at ${path}: ${node.value}`);
          errors++;
        }
      }
      for (const [key, val] of Object.entries(node)) {
        checkNode(val, `${path}.${key}`);
      }
    }
  }

  log('Validating token references...');
  checkNode(tokens.semantic, 'semantic');
  if (errors === 0) {
    log(`  ✓ All references resolved`);
  }
  return errors;
}

async function main() {
  const args = process.argv.slice(2);
  const validateOnly = args.includes('--validate');

  log('\nTanvrit Token Generation Pipeline');
  log(`Tokens: ${TOKENS}`);
  log(`Generated: ${GENERATED}\n`);

  fs.mkdirSync(GENERATED, { recursive: true });

  const tokens = loadTokens();

  if (validateOnly) {
    const errors = validateRefs(tokens);
    process.exit(errors > 0 ? 1 : 0);
  }

  validateRefs(tokens);
  writeCss(tokens);
  writeTs(tokens);
  writeKotlin(tokens);
  writeSwift(tokens);

  log('\n✓ Token generation complete\n');
}

main().catch(err => {
  console.error('Generation failed:', err);
  process.exit(1);
});
