from unittest.mock import ANY, call

from ddt import data, ddt
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.test import override_settings

from core.file_uploads.upload_service import FileUploader, file_uploader
from core.tests.testcases import UnitTestCase


@ddt
class FileUploaderTest(UnitTestCase):
    def test_invalid_encryption_algorithm(self):
        with override_settings(ENCRYPTION_ALGORITHM="non_existent"), self.assertRaisesMessage(
            Exception, "'non_existent' is not a valid hashlib encryption algorithm."
        ):
            FileUploader()

    def test_invalid_file_upload_class(self):
        with override_settings(FILE_UPLOAD_CLASS="non_existent"), self.assertRaisesMessage(
            Exception, "non_existent doesn't look like a module path"
        ):
            FileUploader()

    @data(
        # file extension
        "jpg",
        "jpeg",
    )
    def test_upload_metadata(self, file_extension):
        file = InMemoryUploadedFile(
            file=open("core/tests/test_file.jpeg", "rb"),
            name=f"testfile.{file_extension}",
            field_name="file",
            size="0",
            content_type="image/jpeg",
            charset=None,
        )
        metadata = {
            "logo": file,
            "some": "other",
            "interesting": "data",
        }

        res = file_uploader.upload_metadata(metadata=metadata, storage_destination="store/here")

        file_uploader.file_upload_class.upload_file.assert_has_calls(
            [
                call(file=ANY, storage_destination="store/here/logo_small.jpeg"),
                call(file=ANY, storage_destination="store/here/logo_medium.jpeg"),
                call(file=ANY, storage_destination="store/here/logo_large.jpeg"),
                call(file=ANY, storage_destination="store/here/metadata.json"),
            ]
        )
        self.assertDictEqual(
            res,
            {
                "images": {
                    "logo": {
                        "content_type": "image/jpeg",
                        "large": {"url": "https://some_storage.some_region.com/store/here/logo_large.jpeg"},
                        "medium": {"url": "https://some_storage.some_region.com/store/here/logo_medium.jpeg"},
                        "small": {"url": "https://some_storage.some_region.com/store/here/logo_small.jpeg"},
                    }
                },
                "interesting": "data",
                "metadata_hash": "491a300727047f4ac6d0ecf87629904292e8b38ae6e19c40947dabeffb78214a",
                "metadata_url": "https://some_storage.some_region.com/store/here/metadata.json",
                "some": "other",
            },
        )
        file.close()
