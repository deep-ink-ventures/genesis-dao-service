#!/bin/sh
set -e


if [  "$1" = "web" ]; then
  python manage.py migrate
  daphne -b 0.0.0.0 -p 8000 service.asgi:application
fi

exec "$@"
