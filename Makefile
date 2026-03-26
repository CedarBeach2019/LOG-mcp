.PHONY: test lint run

test:
	python -m pytest tests/ -q

lint:
	@echo "no linter configured"

run:
	python -m gateway.server
