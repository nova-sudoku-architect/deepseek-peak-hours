# Hosting & distribution

Three independent channels. They are **not** mutually exclusive — the GitHub
repo is always the source of truth, and the registries are just delivery.

```
                    ┌──────────────────────────────┐
                    │  github.com/novaclawhk/      │
                    │      deepseek-peak-hours     │   ← source of truth
                    └───────────┬──────────────────┘
                                │
          ┌─────────────────────┼─────────────────────┐
          │                     │                     │
          ▼                     ▼                     ▼
   ┌────────────┐       ┌──────────────┐      ┌──────────────┐
   │    PyPI    │       │     npm      │      │   Releases   │
   │            │       │              │      │  (tarballs)  │
   └─────┬──────┘       └──────┬───────┘      └──────┬───────┘
         │                     │                     │
    pip install          npm install           direct URL
    pipx install         npx dspeak            install
```

---

## 1. GitHub only (zero signup cost, works today)

Nothing needs to be published anywhere. Anyone can install straight from the
repo:

```bash
# Python — from source
pip install "git+https://github.com/novaclawhk/deepseek-peak-hours.git"
pipx install "git+https://github.com/novaclawhk/deepseek-peak-hours.git"

# Python — pin a tag
pip install "git+https://github.com/novaclawhk/deepseek-peak-hours.git@v1.0.0"

# Python — from a Release asset (no git needed)
pip install https://github.com/novaclawhk/deepseek-peak-hours/releases/download/v1.0.0/deepseek_peak_hours-1.0.0-py3-none-any.whl
```

```bash
# npm — from a packed tarball attached to a Release
npm i https://github.com/novaclawhk/deepseek-peak-hours/releases/download/v1.0.0/deepseek-peak-hours-1.0.0.tgz
```

> **Caveat:** `npm install github:novaclawhk/deepseek-peak-hours` will *not*
> work for this repo, because `package.json` lives in `npm/`, not at the repo
> root. Use the Release tarball above, or publish to the npm registry.

To attach the tarball to a Release:

```bash
cd npm && npm pack            # → deepseek-peak-hours-1.0.0.tgz
gh release create v1.0.0 \
  dist/*.whl dist/*.tar.gz npm/*.tgz \
  --title "v1.0.0" --generate-notes
```

**Good for:** private/internal use, air-gapped installs, avoiding registry
signup. **Not good for:** casual users who expect `pip install <name>`.

---

## 2. PyPI (`pip install deepseek-peak-hours`)

Free for public packages. Two ways to authenticate:

### a) Trusted Publishing (recommended — no secrets in the repo)

PyPI can trust a specific GitHub Actions workflow via OIDC, so no API token is
ever stored. One-time setup:

1. Create the account at <https://pypi.org/account/register/> (verify email,
   enable 2FA — 2FA is mandatory).
2. Go to <https://pypi.org/manage/account/publishing/> → **Add a new pending
   publisher**:
   - PyPI project name: `deepseek-peak-hours`
   - Owner: `novaclawhk`
   - Repository: `deepseek-peak-hours`
   - Workflow name: `release.yml`
   - Environment: `pypi`
3. Push a tag — the workflow below publishes with no credentials at all.

```yaml
# .github/workflows/release.yml
name: release

on:
  push:
    tags: ["v*"]

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - run: pip install build && python -m build
      - uses: actions/upload-artifact@v4
        with: { name: dist, path: dist/ }

  pypi:
    needs: build
    runs-on: ubuntu-latest
    environment: pypi
    permissions:
      id-token: write        # ← the OIDC token PyPI trusts
    steps:
      - uses: actions/download-artifact@v4
        with: { name: dist, path: dist/ }
      - uses: pypa/gh-action-pypi-publish@release/v1
```

### b) API token

```bash
python -m build
twine check dist/*
TWINE_USERNAME=__token__ TWINE_PASSWORD=pypi-AgEIcHlwaS5vcmc... \
  twine upload dist/*
```

Store the token as a GitHub secret (`PYPI_API_TOKEN`), never in the repo.

**Notes**
- The name `deepseek-peak-hours` must be free on PyPI. Check first:
  `pip index versions deepseek-peak-hours` or the PyPI search box. If taken,
  use a scoped variant (e.g. `dspeak-hours`) and keep the CLI name `dspeak`.
- Filenames are immutable — a version can never be re-uploaded. Bump
  `version` in `pyproject.toml` for every release.
- Deleting a release does not free the name/version.

---

## 3. npm (`npm install deepseek-peak-hours`)

Free for public packages.

```bash
cd npm
npm login                        # opens the browser, or use a token
npm publish --access public      # ← required for scoped names
```

- **Name check:** `npm view deepseek-peak-hours` — if it 404s, the name is free.
  If taken, publish as `@novaclawhk/deepseek-peak-hours` (scoped names need
  `--access public` on first publish).
- **2FA:** the account needs 2FA enabled. Use a *Granular Access Token* with
  "Read and write" + "Bypass 2FA" for CI, stored as `NPM_TOKEN`.
- **`preferGlobal` + `postinstall`:** the wrapper runs a Python install. Keep
  the postinstall non-fatal (it is — it always exits 0) so `npm ci` never
  breaks on a machine without Python.

CI publishing:

```yaml
  npm:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: "20", registry-url: "https://registry.npmjs.org" }
      - run: cd npm && npm publish --access public
        env: { NODE_AUTH_TOKEN: "${{ secrets.NPM_TOKEN }}" }
```

### GitHub Packages as an npm registry

Alternative if you want the package tied to the repo:

```bash
npm config set @novaclawhk:registry https://npm.pkg.github.com
npm publish --registry https://npm.pkg.github.com
```

Downside: **installing requires a GitHub token**, even for public packages. Use
it for private/internal packages only; use npmjs.com for public ones.

---

## Which should you pick?

| Goal | Do this |
|---|---|
| Use it myself / on one machine | GitHub only (`pipx install git+https://…`) |
| Share with a few people I know | GitHub Release assets |
| Anyone can `pip install` it | PyPI + Trusted Publishing |
| JS projects can `npm i` it | npm registry |
| Private company internal | GitHub Packages or a private PyPI index |

Suggested order: **GitHub now → PyPI when the API stabilises → npm once someone
actually wants the JS wrapper.**

---

## Versioning checklist

1. Bump `version` in `pyproject.toml` **and** `npm/package.json` (keep them equal).
2. Rebuild and re-vendor the wheel so the npm package ships the new code:
   ```bash
   python -m build
   cp dist/deepseek_peak_hours-*.whl npm/vendor/
   ```
3. `pytest` → all green.
4. `git tag v1.0.1 && git push --tags` → the release workflow publishes.
