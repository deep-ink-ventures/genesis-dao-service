#!/bin/sh
set -e

if [  "$1" = "web" ]; then
  python manage.py migrate
  daphne -b 0.0.0.0 -p 8000 service.asgi:application

elif [ "$1" = "worker" ]; then
  set -- celery \
          -A service worker \
          -l INFO \
          -Q celery \
          --autoscale=10,1
fi

exec "$@"
