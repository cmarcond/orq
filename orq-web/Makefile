.PHONY: test lint fmt test-py test-js qa qa-smoke
test: test-py test-js
test-py: ; pytest
test-js: ; node --test 'tests/js/*.test.mjs'
lint: ; ruff check . && ruff format --check .
fmt: ; ruff format . && ruff check --fix .
qa: ; pytest qa
qa-smoke: ; pytest qa/tests/test_smoke.py
