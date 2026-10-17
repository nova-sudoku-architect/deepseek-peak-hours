# deepseek-peak-hours (npm)

Node-friendly wrapper so **JavaScript projects can depend on the same tool**:

```bash
npm i deepseek-peak-hours     # local dependency
npm i -g deepseek-peak-hours  # global CLI
npx deepseek-peak-hours       # one-shot
```

```js
const { execFileSync } = require('child_process');

let peak;
try {
  execFileSync('npx', ['--no-install', 'dspeak', '--quiet'], { stdio: 'pipe' });
  peak = true;                    // exit 0
} catch (err) {
  peak = err.status !== 1;        // exit 1 = off-peak, anything else = error
}
```

Requires **Python ≥ 3.9** on the machine. `postinstall` installs the bundled
wheel into a private venv (`~/.cache/deepseek-peak-hours/venv`) — no network, no
touching your global site-packages. Override the interpreter with
`DSPEAK_PYTHON=/path/to/python3`.

Full docs, exit codes and config reference: see the
[repository README](https://github.com/nova-sudoku-architect/deepseek-peak-hours#readme).
