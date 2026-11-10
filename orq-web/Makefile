.PHONY: test lint fmt test-py test-js qa qa-smoke qa-rede
test: test-py test-js
test-py: ; pytest
test-js: ; node --test 'tests/js/*.test.mjs'
lint: ; ruff check . && ruff format --check .
fmt: ; ruff format . && ruff check --fix .
qa: ; pytest qa
qa-smoke: ; pytest qa/tests/test_smoke.py
qa-rede: ; ORQ_QA_URL=$${ORQ_QA_URL:-http://10.0.0.10:8765/} ORQ_QA_VIA=$${ORQ_QA_VIA:-server0} pytest qa/tests/test_smoke.py
