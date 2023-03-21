#!/bin/sh
set -e

if [  "$1" = "web" ]; then
  daphne -b 0.0.0.0 -p 8000 service.asgi:application

elif [ "$1" = "worker" ]; then
  python manage.py migrate
  python manage.py collectstatic
  set -- celery \
          -A service worker \
          -l INFO \
          -Q celery \
          --autoscale=10,1
fi

exec "$@"
