# Developer guide — TubeDub / VideoMonster V2

## Prerequisites

- Python **3.10+**
- **FFmpeg** in `PATH`
- (Optional) `transformers` + `torch` for offline Marian/NLLB
- (Optional) `argostranslate` for offline Argos MT

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
pip install pytest ruff          # dev tools
cp .env.example .env               # optional overrides
```

Windows shortcut: `scripts\dev.ps1 install`

## Run

```bash
python app.py
# or
make run
```

Desktop shell: `python desktop.py`

## Tests

```bash
make test              # pytest (fast CI subset)
make test-all          # all scripts/test_*.py
run_smoke_test.bat     # import + e2e + ZIP
```

Enable developer mode in UI (🔧 Dev) or `VM_DEV_MODE=1` for experimental modules.

## Project layout

```
api/          Flask blueprints (thin HTTP layer)
engines/      Business logic, pipelines, model manager
templates/    Jinja pages
static/       CSS/JS
data/         JSON config (feature flags, module registry)
scripts/      CLI tools + legacy test scripts
tests/        Pytest suite (CI)
docs/         Technical specifications (TZ)
```

**Rule:** batch dub pipeline (`api/auto_dub_api.py`) is the stability baseline. New modules go under `engines/`, behind feature flags, OFF by default.

## Lint

```bash
make lint
ruff check api engines tests
```

## Release ZIP

```bash
make zip
# or scripts\create_zip.ps1
```

Excludes `cache/`, `models/`, `output/`, `uploads/`, secrets.

## CI

GitHub Actions: `.github/workflows/ci.yml` — ruff + pytest on push/PR.

## Secrets

Never commit:

- `data/license_secret.txt`
- `license.json`
- `.env`

Use `.env.example` as reference.
