.PHONY: venv build build-image test test clean

SHELL := bash

venv:
	@if [ -z $(python-location) ]; then \
		virtualenv venv; \
	else \
		virtualenv --python $(python-location) venv; \
		./venv/bin/pip3 install --upgrade pip; \
	fi;

build:
	pip install --upgrade pip
	pip install -r requirements/dev.txt
	pre-commit install

build-image:
	docker compose build

run-migration:
	@if [ $(use-docker) ]; then \
		docker compose run --rm web sh -c "sleep 15; python manage.py migrate"; \
	else \
		python manage.py migrate; \
	fi;

format:
	black .
	isort .

start-postgres:
	docker compose up -d postgres

start-redis:
	docker compose up -d redis

start-databases: start-redis start-postgres

start-web:
	@if [ $(use-docker) ]; then \
		docker compose up -d web; \
	else \
		source .env; \
		python manage.py runserver; \
	fi;

start-dev: run-migration run-populate run-setup start-web

test:
	@if [ $(use-docker) ]; then \
		docker compose run --rm web sh -c "sleep 15; python manage.py migrate"; \
		docker compose run -e "COVERAGE_FILES=$(coverage_files)" --rm web sh bin/run_tests_with_coverage.sh; \
	else \
	  	set -a; source .env; set +a; \
		sh bin/run_tests_with_coverage.sh; \
	fi;


test-with-coverage:
	./bin/run_tests_with_coverage.sh

test-unit:
	@./manage.py test -v=3 --tag=unit

test-integration:
	@./manage.py test -v=3 --tag=integration $(arg)

check-style:
	pip install pre-commit
	pre-commit run --all-files --show-diff-on-failure

terminate-django:
	@kill -9 $(runserver_pids) 2> /dev/null || true

clean-docker:
	docker compose down --remove-orphans --volumes

clean-venv:
	rm -rf venv

clean: terminate-django clean-docker clean-venv
