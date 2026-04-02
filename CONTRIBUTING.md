# Contributing

Thanks for helping improve Novel Generator.

## Development Setup

1. Install Python 3.11 or newer.
2. Create a virtual environment.
3. Install the project in editable mode with development dependencies.
4. Copy `.env.example` to `.env`.
5. Start the web app and worker in separate terminals.

```bash
python -m venv .venv
python -m pip install --upgrade pip
python -m pip install -e .[dev]
uvicorn novel_generator.main:app --reload
python -m novel_generator.worker
pytest
```

## Before Opening a Pull Request

- Run `pytest`.
- Keep changes focused and documented.
- Update docs when behavior, setup, or configuration changes.
- Add or update tests when you change behavior.

## Pull Request Expectations

- Explain the user-visible change.
- Mention any schema, environment, or deployment impact.
- Include screenshots or terminal output for UI or workflow changes when practical.

## Scope

Please open an issue before starting a large feature or architectural rewrite. Small bug fixes, tests, docs improvements, and deployment polish are welcome without pre-approval.
