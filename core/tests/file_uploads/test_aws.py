from unittest.mock import Mock, patch

from botocore.exceptions import ClientError

from core.file_uploads.aws import s3_client
from core.tests.testcases import UnitTestCase


class AWSTest(UnitTestCase):
    def setUp(self):
        self.s3_client = s3_client
        self.s3_client.bucket_name = "bucket1"
        self.s3_client.region_name = "region1"
        self.s3_client.client = Mock()
        self.file = Mock()

    def test_aws(self):
        res = self.s3_client.upload_file(file=self.file, storage_destination="store/here")

        self.s3_client.client.upload_fileobj.assert_called_once_with(
            self.file, "bucket1", "store/here", ExtraArgs={"ACL": "public-read"}
        )
        self.assertEqual(res, "https://bucket1.s3.region1.amazonaws.com/store/here")

    @patch("core.file_uploads.aws.logger")
    def test_aws_fail(self, logger_mock):
        self.s3_client.client.upload_fileobj.side_effect = ClientError({"Error": {"Code": 123}}, "upload_fileobj")

        res = self.s3_client.upload_file(file=self.file, storage_destination="store/here")

        self.s3_client.client.upload_fileobj.assert_called_once_with(
            self.file, "bucket1", "store/here", ExtraArgs={"ACL": "public-read"}
        )
        self.assertIsNone(res)
        logger_mock.exception.assert_called_once_with("Error while uploading metadata to s3.")
