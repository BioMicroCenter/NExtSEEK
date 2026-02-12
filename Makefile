.PHONY: coverage-all

coverage-all: ## Merge vitest + playwright coverage into unified HTML report
	npx vitest run --coverage
	USE_COVERAGE=true npx playwright test --project=mock
	mkdir -p coverage/merged
	npx nyc merge coverage/.nyc_output coverage/merged/coverage.json
	npx nyc report --reporter=html --reporter=text --temp-dir=coverage/merged -r coverage/report
