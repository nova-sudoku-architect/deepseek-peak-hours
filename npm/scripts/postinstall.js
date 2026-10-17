#!/usr/bin/env node
'use strict';

/**
 * postinstall — install the bundled Python wheel into a private venv.
 *
 * Strategy, cheapest first:
 *   1. A python that already imports deepseek_peak_hours → done, no work.
 *   2. An adjacent `.venv` inside the package dir (pip setup) → the wheel is
 *      already there, nothing to do.
 *   3. Create `$XDG_CACHE_HOME/deepseek-peak-hours/venv` and pip-install the
 *      vendored wheel into it — no network, no collision with the user's
 *      site-packages, no PEP 668 "externally managed" errors.
 *
 * Failure is non-fatal: the CLI shim retries on first run and can fall back to
 * any python that already has the package.
 */

const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');
const {
  PKG_ROOT,
  bootstrapPython,
  cacheVenv,
  resolvePython,
  venvPython,
} = require('../lib/python');

function findWheel() {
  const dir = path.join(PKG_ROOT, 'vendor');
  if (!fs.existsSync(dir)) return null;
  const wheels = fs.readdirSync(dir).filter((f) => f.endsWith('.whl'));
  if (!wheels.length) return null;
  // Highest version wins if several are bundled.
  wheels.sort();
  return path.join(dir, wheels[wheels.length - 1]);
}

function run(cmd, args, opts = {}) {
  return spawnSync(cmd, args, {
    stdio: opts.quiet ? 'ignore' : 'inherit',
    timeout: opts.timeout || 180000,
    env: process.env,
  });
}

function install({ quiet = false } = {}) {
  const log = (msg) => {
    if (!quiet) process.stdout.write(msg + '\n');
  };

  // 1. Already importable?
  if (resolvePython()) {
    log('deepseek-peak-hours: python package already present');
    return true;
  }

  const wheel = findWheel();
  if (!wheel) {
    if (!quiet) {
      process.stderr.write(
        'deepseek-peak-hours: no vendored wheel found; skipping python install\n'
      );
    }
    return false;
  }

  const base = bootstrapPython();
  if (!base) {
    if (!quiet) {
      process.stderr.write(
        'deepseek-peak-hours: no python3 found on PATH — the CLI needs Python >= 3.9\n'
      );
    }
    return false;
  }

  const venvDir = cacheVenv();
  const py = venvPython(venvDir);

  if (!fs.existsSync(py)) {
    log(`deepseek-peak-hours: creating venv at ${venvDir}`);
    fs.mkdirSync(path.dirname(venvDir), { recursive: true });
    const made = run(base, ['-m', 'venv', venvDir], { quiet });
    if (made.status !== 0) {
      if (!quiet) {
        process.stderr.write(
          'deepseek-peak-hours: could not create a venv (missing python3-venv?)\n'
        );
      }
      return false;
    }
  }

  log('deepseek-peak-hours: installing bundled wheel');
  const pip = run(py, ['-m', 'pip', 'install', '--quiet', '--upgrade',
                       '--no-warn-script-location', wheel], { quiet });
  if (pip.status !== 0) {
    if (!quiet) {
      process.stderr.write(
        'deepseek-peak-hours: pip install of the bundled wheel failed\n'
      );
    }
    return false;
  }

  if (!resolvePython()) {
    if (!quiet) {
      process.stderr.write('deepseek-peak-hours: install finished but import failed\n');
    }
    return false;
  }
  log('deepseek-peak-hours: installed ✓');
  return true;
}

if (require.main === module) {
  try {
    const ok = install();
    // Never fail the npm install — the shim can still recover later.
    process.exit(ok ? 0 : 0);
  } catch (err) {
    if (process.env.DSPEAK_DEBUG) {
      process.stderr.write(String(err && err.stack) + '\n');
    }
    process.exit(0);
  }
}

module.exports = { install, findWheel };
