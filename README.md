# Genesis Dao Service

## Prerequisites

- Install Python 3.11 or greater
- Install pip and virtualenv
- Install sqlite-devel / libsqlite3-dev
- Docker daemon must be available for development

## Installation
### Setup

```shell
cp .env.example .env
```

Adjust the `.env` values according to your needs,_source_ it, so it is available in your current environment.

```shell
source .env
```

If using zsh (for example, on macOS), you may need to mark variables for export,
before calling any `make` target:

```shell
set -a; source .env; set +a
```

### Create and activate Python venv

```shell
make venv
```

Activate this virtual environment:

```shell
source venv/bin/activate
```

Execute `deactivate` to exit out of the virtual environment.


### Get and Build Python Requirements

Then use the `build` Make target to get all requirements fetched and compiled:

```shell
make build
```

### Start databases

The following starts the data stores used by this project: PostgreSQL.

Make sure you have:

1. Docker running
2. and the [environment is loaded](#setup)

```shell
make start-postgres
```

To stop the data stores, use Docker Compose:

```shell
docker-compose down postgres
```

### Start App

```shell
make start-dev
```

Some make commands can be used w/ or w/o docker for the app container:
  - `start-app`, `start-dev`, `test`, `run-migration` 
  - (when using docker the envvar `DATABASE_HOST` has to point to the name of the postgres container:
    - `DATABASE_HOST=postgres`)
- e.g.:
```shell
make test
make test use-docker=true
```

## Syncing the Database with the Blockchain
If your blockchain is running and [setup in .env](#setup) under `BLOCKCHAIN_URL` you can start the event listener by running:
```shell
make start-listener
```
It will sync the database with the chain and try to fetch a new block every `BLOCK_CREATION_INTERVAL` seconds.

## Documentation

API documentation: `/redoc/`
- default: http://127.0.0.1:8000/redoc/

## Environments Variables
### Base Setup
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
### Blockchain
- BLOCKCHAIN_URL
  - type: str
- BLOCK_CREATION_INTERVAL
  - type: int
  - default: `6` seconds
  - minimum time the event listener waits before trying to fetch the newest block from the chain
### Storage
- FILE_UPLOAD_CLASS:
  - type: str
  - default: `"core.file_uploads.aws.s3_client"`
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
- these are only required when using the default `FILE_UPLOAD_CLASS`:
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