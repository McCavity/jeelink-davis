# Contributing

Thank you for your interest in jeelink-davis! Contributions are welcome — bug reports, documentation improvements, and code changes alike.

## Before you start

This project is primarily developed and tested on a **Raspberry Pi** with a physical Davis Vantage Pro 2 ISS and JeeLink USB receiver. If you do not have this hardware, you can still contribute to the web dashboard, data processing logic, and documentation — the test suite mocks the serial port and runs without any hardware.

## Development setup

```bash
git clone https://github.com/McCavity/jeelink-davis.git
cd jeelink-davis
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,web]"
```

Copy the example config and fill in your values:

```bash
cp config.toml.example config.toml
# edit config.toml
```

Run the test suite:

```bash
.venv/bin/pytest tests/ -v
```

## Submitting changes

1. **Fork** the repository and create a branch from `main`.
2. Make your changes. Keep commits focused — one logical change per commit.
3. If you fix a bug, add a test that reproduces it.
4. Run `pytest tests/ -v` and confirm all tests pass.
5. Open a **pull request** against `main` with a clear description of what the change does and why.

## Reporting bugs

Use the [Bug Report issue template](.github/ISSUE_TEMPLATE/bug_report.md). Please include:
- Your hardware (JeeLink firmware version, Davis ISS model/region)
- Python version and OS
- Relevant log output from `journalctl -u davis-weather` or the console

## Code style

- Python 3.11+, standard library where possible
- No external dependencies added without discussion
- Existing formatting conventions (no formatter enforced, but keep style consistent with the file you are editing)

## Questions

Open a [GitHub Discussion](https://github.com/McCavity/jeelink-davis/discussions) for anything that is not a bug or feature request.
