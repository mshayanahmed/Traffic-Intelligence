# Contributing

Thank you for your interest in improving Traffic Intelligence.

## Development workflow

1. Fork the repository and create a feature branch.
2. Create a virtual environment and install dependencies.
3. Make focused, testable changes.
4. Run the relevant test suite before opening a pull request.
5. Submit a pull request with a clear summary and validation steps.

## Local setup

```bash
git clone https://github.com/mshayanahmed/Traffic-Intelligence.git
cd Traffic-Intelligence
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Environment files

- Do not commit `.env` files, API keys, local database files, or generated evidence.
- Use `.env.example` as the safe template for configuration.
- Keep secrets in your local environment and add them to `.gitignore` if needed.

## Coding expectations

- Keep changes small and scoped to the relevant feature or bug.
- Prefer readable, well-structured code.
- Add or update tests when changing behavior.
- Update documentation when the user-facing workflow changes.

## Pull requests

Please include:

- a concise summary of the work
- the reasoning behind the change
- tests or validation steps you ran
- any note about edge cases or follow-up work

## Questions

Open a discussion or issue if you need clarification before starting work.
