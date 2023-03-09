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
- start containers and app
```angular2html
make start-dev
```
some make commands can be used w/ or w/o docker for the app container:
  - `start-app`, `start-dev`, `test`, `run-migration` 
  - (when using docker the envvar `DATABASE_HOST` has to point to the name of the postgres container:
    - `DATABASE_HOST=postgres`)
- e.g.:
```angular2html
make test
make test use-docker=true
```

## Documentation

API documentation: `/redoc/`
- default: http://127.0.0.1:8000/redoc/


## Environments Variables
- APPLICATION_STAGE
  - type: str
  - default: `development`
  - use `production` for prod
- BASE_PORT
  - type: int
  - default: `8000`
- BASE_URL
  - type: str
  - default: `http://127.0.0.1:8000`
- DATABASE_HOST
  - type: str
  - default: `0.0.0.0`
  - use `postgres` when working w/ docker
- DATABASE_NAME
  - type: str
  - default: `core`
- DATABASE_PORT
  - type: int
  - default: `5432`
- DATABASE_USER
  - type: str
  - default: `postgres`
- DATABASE_PASSWORD
  - type: str
  - default: `postgres`
- FILE_UPLOAD_CLASS:
  - type: str
  - default: "core.file_uploads.aws.s3_client"
  - Class used to upload metadata. Requires a method: 
    ```    
    def upload_file(self, file, storage_destination=None) -> Optional[str]:
    """
    Args:
        file: file to upload (file-like obj, readable)
        storage_destination: pathstr / folder name. e.g.: "folder_1/folder_2/my_file.jpeg"
    
    Returns:
        url of uploaded file
    """
    ```
- ENCRYPTION_ALGORITHM:
  - type: str
  - default: "sha3_256"
  - Hashlib encryption algorithm used to hash the uploaded metadata.
- LOGO_SIZES:
  - type: dict
  - default: `{"small": (88, 88), "medium": (104, 104), "large": (124, 124)}`
  - Sizes of Dao logo files (metadata)
- these are only required when using default FILE_UPLOAD_CLASS="core.file_uploads.aws.s3_client":
  - AWS_STORAGE_BUCKET_NAME
    - type: str
    - Name of the AWS bucket to store metadata in.
  - AWS_S3_REGION_NAME
    - type: str
    - AWS region of said bucket.
  - AWS_S3_ACCESS_KEY_ID
    - type: str
    - AWS access key to access said bucket.
  - AWS_S3_SECRET_ACCESS_KEY
    - type: str
    - AWS secret access key to access said bucket.
  - or similar aws authentication method using boto3