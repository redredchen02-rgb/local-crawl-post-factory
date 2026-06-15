.PHONY: install install-browser test demo clean

install:
	python3 -m pip install -e '.[dev]'

install-browser:
	python3 -m pip install -e '.[browser,dev]'
	python3 -m playwright install chromium

test:
	python3 -m pytest -q

demo:
	bash scripts/demo.sh

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache out/demo state/demo.sqlite
