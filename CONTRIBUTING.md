# 🤝 Contributing to LOG-mcp

Welcome! This is a beginner-friendly project. Whether you're fixing a typo, adding a PII pattern, or building a new scout connector — every contribution matters. No prior open-source experience required.

## Table of Contents

- [First Contribution](#first-contribution)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Adding a PII Detection Pattern](#adding-a-pii-detection-pattern)
- [Adding a Scout Connector](#adding-a-scout-connector)
- [Adding Tests](#adding-tests)
- [Code Style](#code-style)
- [PR Checklist](#pr-checklist)
- [Good First Issues](#good-first-issues)

---

## First Contribution

**New to open source?** Here's the full loop:

1. **Find something to work on** — Check [Good First Issues](#good-first-issues) or browse issues labeled `help wanted`
2. **Fork the repo** — Click "Fork" on [GitHub](https://github.com/CedarBeach2019/LOG-mcp), then clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/LOG-mcp.git
   cd LOG-mcp
   ```
3. **Create a branch** — Give it a descriptive name:
   ```bash
   git checkout -b add/zip-code-detection
   ```
4. **Make your changes** — Edit, test, repeat
5. **Run the tests** — Make sure nothing broke:
   ```bash
   pytest tests/ -v
   ```
6. **Commit and push**:
   ```bash
   git add -A
   git commit -m "feat: add ZIP code detection to PII patterns"
   git push origin add/zip-code-detection
   ```
7. **Open a Pull Request** — Go to your fork on GitHub, click "Compare & pull request". Describe what you changed and why.

That's it! We review PRs quickly and will help you through any issues.

---

## Development Setup

```bash
# Clone and enter the project
git clone https://github.com/CedarBeach2019/LOG-mcp.git
cd LOG-mcp

# Install with dev dependencies (includes pytest, ruff, coverage)
pip install -e ".[dev]"

# Initialize the local vault (needed for CLI tests)
log init

# Run tests to verify everything works
pytest tests/ -v
```

You should see 52+ unit tests pass. If something fails, open an issue — we'll fix it.

---

## Project Structure

```
LOG-mcp/
├── vault/              # Core engine
│   ├── core.py         # Dehydrator, Rehydrator, RealLog, PII detection
│   ├── cli.py          # CLI entry point (click commands)
│   ├── archiver.py     # Long-term session archival
│   └── reallog_db.py   # SQLite schema and migrations
├── mcp/
│   └── server.py       # MCP server (Model Context Protocol)
├── scouts/             # AI provider connectors
│   ├── base.py         # Base scout class
│   ├── claude.py       # Anthropic Claude connector
│   └── deepseek_scout.py
├── cloudflare/         # Cloudflare Worker deployment
│   └── worker.js       # Edge gateway with D1 + KV
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── tests/
│   ├── test_*.py       # Unit tests
│   └── demo_e2e.py     # E2e scenario suite (46 checks)
└── scripts/
    └── init.sh         # Vault initialization script
```

---

## Adding a PII Detection Pattern

Want to catch a new type of sensitive data? Here's how.

### Example: Adding ZIP code detection

**1. Add the pattern to `vault/core.py`**

Find the `PII_PATTERNS` dictionary in the `Dehydrator` class and add your pattern:

```python
# In vault/core.py, inside Dehydrator.__init__ or wherever PII_PATTERNS is defined:

self.patterns = {
    # ... existing patterns ...
    'zip_code': r'\b\d{5}(-\d{4})?\b',  # 12345 or 12345-6789
}
```

**2. Add the entity mapping**

In the same class, add the replacement token format:

```python
self.entity_labels = {
    # ... existing labels ...
    'zip_code': 'ZIP',
}
```

**3. Write a test**

Create or add to `tests/test_pii_patterns.py`:

```python
import pytest
from vault.core import Dehydrator, RealLog
import tempfile


class TestZipCodeDetection:
    def setup_method(self):
        self.db_path = tempfile.mktemp(suffix=".db")
        self.vault = RealLog(self.db_path)
        self.dehydrator = Dehydrator(self.vault)

    def test_detects_five_digit_zip(self):
        result = self.dehydrator.dehydrate("Ship to 90210")
        assert "ZIP_1" in result.dehydrated
        assert "90210" not in result.dehydrated

    def test_detects_zip_plus_four(self):
        result = self.dehydrator.dehydrate("ZIP: 90210-1234")
        assert "ZIP_1" in result.dehydrated

    def test_does_not_false_positive_on_years(self):
        result = self.dehydrator.dehydrate("Founded in 1999")
        # If 5-digit year-like strings aren't zips, ensure no false match
        # (you may need to add contextual logic)
        assert "ZIP_1" not in result.dehydrated
```

**4. Run and verify**

```bash
pytest tests/test_pii_patterns.py -v
```

**5. Commit and PR**

```bash
git add vault/core.py tests/test_pii_patterns.py
git commit -m "feat: add ZIP code detection to PII patterns"
git push
```

### Tips for PII patterns

- **Be specific.** `\b\d{5}\b` matches years, IDs, and more. Add context constraints.
- **Test edge cases.** What about ZIP codes in addresses? In phone numbers? Embedded in other numbers?
- **Check false positives.** Run the full e2e suite: `pytest tests/demo_e2e.py -v`
- **Non-English PII?** Add patterns for non-ASCII character ranges. The existing detector already handles Cyrillic and CJK.

---

## Adding a Scout Connector

"Scouts" are AI provider connectors that send anonymized text to an LLM and return responses. See `scouts/base.py` for the interface, and `scouts/deepseek_scout.py` as a reference implementation.

1. Create `scouts/your_provider.py` extending `BaseScout`
2. Implement `query()` method
3. Add tests in `tests/`
4. Submit a PR

---

## Adding Tests

### Unit tests

Place in `tests/test_<module>.py`. Use descriptive names:

```python
def test_dehydrate_removes_emails_from_multiline_text():
    """Emails in multiline input should all be detected and replaced."""
    dehydrator = Dehydrator(vault)
    result = dehydrator.dehydrate("Contact alice@ex.com\nOr bob@test.org")
    assert "EMAIL_1" in result.dehydrated
    assert "EMAIL_2" in result.dehydrated
    assert "alice@ex.com" not in result.dehydrated
```

### E2e scenario tests

Add to `tests/demo_e2e.py` following the existing pattern:

```python
def test_healthcare_hipaa_scenario(self):
    """Patient intake form with full PII should be fully anonymized."""
    input_text = "Patient: Maria Garcia, DOB: 04/15/1988, SSN: 543-21-6789"
    result = self.dehydrator.dehydrate(input_text)
    self.record("HIPAA patient intake", result.passed,
                "All PII replaced" if result.passed else f"Missed: {result.missed}")
```

### Running tests

```bash
# All unit tests
pytest tests/ -v

# Specific file
pytest tests/test_pii_patterns.py -v

# With coverage report
pytest --cov=vault --cov=mcp --cov=scouts --cov-report=term-missing

# E2e scenarios only
pytest tests/demo_e2e.py -v
```

---

## Code Style

We use [Ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Check for issues
ruff check .

# Auto-fix what can be fixed
ruff check --fix .

# Format code
ruff format .
```

Rules:
- **Target:** Python 3.10+
- **Line length:** 100 characters
- **Imports:** Sorted (isort via ruff)
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes
- **Docstrings:** Google style on public classes and functions
- **Type hints:** Use them on public APIs, optional on internal code
- **No print() in production code** — use `logging.getLogger(__name__)`

---

## PR Checklist

Before submitting, make sure:

- [ ] All tests pass: `pytest tests/ -v`
- [ ] E2e scenarios pass: `pytest tests/demo_e2e.py -v`
- [ ] Code passes linting: `ruff check .`
- [ ] New features have tests
- [ ] Documentation updated (README.md, QUICKSTART.md, or inline docstrings)
- [ ] Commit messages follow [conventional commits](https://www.conventionalcommits.org/):
  - `feat:` new feature
  - `fix:` bug fix
  - `docs:` documentation
  - `test:` tests
  - `refactor:` code restructuring
  - `chore:` maintenance

---

## Good First Issues

Look for issues labeled:

- `good first issue` — Perfect starting point, well-scoped
- `help wanted` — Needs a hand, may require more context
- `enhancement` — New features and improvements

Don't see one that fits? Open an issue describing what you'd like to work on, and we'll help you scope it.

---

## Questions?

Open a [GitHub Discussion](https://github.com/CedarBeach2019/LOG-mcp/discussions) or just ask in a PR. We're here to help.
