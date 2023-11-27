ALLOWED_HOSTS = ["*"]

try:
    from .local import *  # noqa
except ImportError:
    pass
