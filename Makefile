.PHONY: test lint fmt test-py test-js
test: test-py test-js
test-py: ; pytest
test-js: ; node --test 'tests/js/*.test.mjs'
lint: ; ruff check . && ruff format --check .
fmt: ; ruff format . && ruff check --fix .
