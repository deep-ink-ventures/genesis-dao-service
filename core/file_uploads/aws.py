import logging
from typing import Optional

import boto3
from botocore.exceptions import ClientError

from settings import settings

logger = logging.getLogger("alerts")


class S3Client:
    def __init__(self):
        self.client = boto3.client(
            service_name="s3",
            aws_access_key_id=settings.AWS_S3_ACCESS_KEY_ID,
            aws_secret_access_key=settings.AWS_S3_SECRET_ACCESS_KEY,
            region_name=settings.AWS_S3_REGION_NAME,
        )
        self.bucket_name = settings.AWS_STORAGE_BUCKET_NAME
        self.region_name = settings.AWS_S3_REGION_NAME

    def upload_file(self, file, storage_destination) -> Optional[str]:
        """
        Args:
            file: file to upload (file-like obj, readable)
            storage_destination: storage_destination

        Returns:
            url of uploaded file

        uploads file to s3
        """
        try:
            self.client.upload_fileobj(file, self.bucket_name, storage_destination, ExtraArgs={"ACL": "public-read"})
        except ClientError:
            logger.exception("Error while uploading metadata to s3.")
            return
        return f"https://{self.bucket_name}.s3.{self.region_name}.amazonaws.com/{storage_destination}"


s3_client = S3Client()
