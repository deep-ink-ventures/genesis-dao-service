from settings.settings import BASE_DIR

TESTING = True

MEDIA_URL = "/test-media/"
MEDIA_ROOT = BASE_DIR / "test-media"

AWS_STORAGE_BUCKET_NAME = None
AWS_S3_ACCESS_KEY_ID = None
AWS_S3_SECRET_ACCESS_KEY = None
AWS_S3_REGION_NAME = None

FILE_UPLOAD_CLASS = "core.file_uploads.test.file_uploader_mock"
