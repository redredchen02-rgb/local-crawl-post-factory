.PHONY: install install-browser install-webui test test-fast test-slow test-full lint typecheck cov demo webui vendor-htmx clean

install:
	python3 -m pip install -e '.[dev]'

install-browser:
	python3 -m pip install -e '.[browser,dev]'
	python3 -m playwright install chromium

install-webui:
	python3 -m pip install -e '.[webui,dev]'

vendor-htmx:
	curl -L https://unpkg.com/htmx.org@1.9.12/dist/htmx.min.js -o webui/static/htmx.min.js

webui:
	crawl-post-webui  # http://127.0.0.1:8000

test:
	python3 -m pytest -q

test-fast:
	python3 -m pytest -m "not slow and not browser and not integration and not subprocess" -q

test-slow:
	python3 -m pytest -m "slow or browser or integration or subprocess" -q -rs

test-full:
	python3 -m pytest -q --cov=core --cov=src --cov=browser --cov=webui --cov-report=term-missing

lint:
	python3 -m ruff check .

typecheck:  # blocking gate — mypy must pass (CI enforces this)
	python3 -m mypy

cov:
	python3 -m pytest --cov=core --cov=src --cov=browser --cov=webui --cov-report=term-missing

demo:
	bash scripts/demo.sh

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache out/demo state/demo.sqlite
