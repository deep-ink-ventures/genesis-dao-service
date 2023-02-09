# Genesis Dao Service

## Prerequisites

- Install Python 3.11 or greater
- Install pip and virtualenv
- Install sqlite-devel / libsqlite3-dev
- Docker daemon must be available for development

## Installation
- create virtualenv
```angular2html
python3 -m venv venv
scource /venv/bin/active
```
- run
```angular2html
make build
```
- start postgres container
```angular2html
make start-postgres
```
- run tests
```angular2html
make test
```