.PHONY: install install-browser install-webui test demo webui vendor-htmx clean

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

demo:
	bash scripts/demo.sh

clean:
	find . -name __pycache__ -type d -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache out/demo state/demo.sqlite
