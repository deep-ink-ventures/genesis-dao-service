import os

DEFAULT_FILE_STORAGE = 'storages.backends.s3boto3.S3Boto3Storage'
STATICFILES_STORAGE = 'storages.backends.s3boto3.S3StaticStorage'
DEBUG = False

SECRET_KEY = os.environ["SECRET"]
AWS_STORAGE_BUCKET_NAME = os.environ.get("AWS_STORAGE_BUCKET_NAME")
AWS_IS_GZIPPED = True
GZIP_CONTENT_TYPES = ("application/pdf",)
