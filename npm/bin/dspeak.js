#!/usr/bin/env node
'use strict';

/**
 * dspeak — thin Node shim around the Python implementation.
 *
 * The Node package exists so JS projects can depend on this tool: same
 * binaries (`dspeak` / `deepseek-peak-hours`), same exit codes, same config
 * file. All logic stays in Python — this file only forwards argv and stdio.
 */

const { spawnSync } = require('child_process');
const { resolvePython, bootstrapPython } = require('../lib/python');

function fail(message, hint) {
  process.stderr.write(`dspeak: ${message}\n`);
  if (hint) process.stderr.write(`hint: ${hint}\n`);
  process.exit(2);
}

let python = resolvePython();

if (!python) {
  // Try a one-shot bootstrap in case postinstall was skipped
  // (e.g. `npm ci --ignore-scripts`).
  try {
    require('../scripts/postinstall.js').install({ quiet: true });
    python = resolvePython();
  } catch (err) {
    fail(
      'Python package not found.',
      'run `npm rebuild deepseek-peak-hours`, or set DSPEAK_PYTHON to a ' +
        'python3 with the package installed (`pip install deepseek-peak-hours`).'
    );
  }
}

if (!python) {
  const anyPython = bootstrapPython();
  if (!anyPython) {
    fail('No Python 3 interpreter found. Install Python >= 3.9.');
  }
  fail(
    'Could not install the Python package.',
    'install it manually: pip install deepseek-peak-hours'
  );
}

const args = ['-m', 'deepseek_peak_hours', ...process.argv.slice(2)];
const res = spawnSync(python, args, {
  stdio: 'inherit',
  env: process.env,
});

if (res.error) {
  if (res.error.code === 'ENOENT') {
    fail(`interpreter disappeared: ${python}`, 'reinstall the npm package');
  }
  fail(res.error.message);
}

if (res.signal) {
  process.kill(process.pid, res.signal);
}

// Preserve the Python exit contract: 0 peak, 1 off-peak, 2 error, 10 changed.
process.exit(res.status === null ? 2 : res.status);
