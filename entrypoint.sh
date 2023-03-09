#!/bin/sh
set -e


if [  "$1" = "app" ]; then
  python manage.py migrate
fi

exec "$@"
