'use strict';

/**
 * Locate the Python interpreter and the venv that holds the package.
 *
 * Precedence for the interpreter:
 *   1. $DSPEAK_PYTHON
 *   2. the venv created by postinstall
 *   3. python3 / python on PATH
 */

const fs = require('fs');
const path = require('path');
const os = require('os');

const PKG_ROOT = path.resolve(__dirname, '..');
const LOCAL_VENV = path.join(PKG_ROOT, '.venv');

function venvPython(venvDir) {
  const win = process.platform === 'win32';
  return win
    ? path.join(venvDir, 'Scripts', 'python.exe')
    : path.join(venvDir, 'bin', 'python3');
}

function cacheVenv() {
  const base = process.env.XDG_CACHE_HOME
    || path.join(os.homedir(), '.cache');
  return path.join(base, 'deepseek-peak-hours', 'venv');
}

function exists(p) {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return true;
  } catch {
    return false;
  }
}

/** Candidate pythons, best first. */
function pythonCandidates() {
  const out = [];
  if (process.env.DSPEAK_PYTHON) out.push(process.env.DSPEAK_PYTHON);
  out.push(venvPython(LOCAL_VENV), venvPython(cacheVenv()));
  out.push('python3', 'python');
  return out;
}

/** The python that can actually import the package, or null. */
function resolvePython() {
  const { spawnSync } = require('child_process');
  for (const cand of pythonCandidates()) {
    const res = spawnSync(
      cand,
      ['-c', 'import deepseek_peak_hours'],
      { stdio: 'ignore', timeout: 20000 }
    );
    if (res.status === 0) return cand;
  }
  return null;
}

/** Any python that runs at all — used for the venv bootstrap. */
function bootstrapPython() {
  const { spawnSync } = require('child_process');
  const cands = [process.env.DSPEAK_PYTHON, 'python3', 'python'].filter(Boolean);
  for (const cand of cands) {
    const res = spawnSync(cand, ['--version'], { stdio: 'ignore', timeout: 20000 });
    if (res.status === 0) return cand;
  }
  return null;
}

function venvPath() {
  return exists(venvPython(LOCAL_VENV)) ? LOCAL_VENV : cacheVenv();
}

module.exports = {
  LOCAL_VENV,
  PKG_ROOT,
  bootstrapPython,
  cacheVenv,
  exists,
  pythonCandidates,
  resolvePython,
  venvPath,
  venvPython,
};
