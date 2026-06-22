import sys
from pathlib import Path


# Make the project root importable so the `cpost` package resolves in tests
# even without an editable install.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: subprocess/network/real-timeout tests (excluded from fast-run)")
    config.addinivalue_line("markers", "browser: Playwright end-to-end browser tests")
