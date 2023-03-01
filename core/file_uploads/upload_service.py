import hashlib
import json
from io import BytesIO

from django.conf import settings
from django.utils.module_loading import import_string
from PIL import Image


class FileUploader:
    file_upload_class = None
    encryption_algorithm = None

    def __init__(self):
        self.file_upload_class = import_string(settings.FILE_UPLOAD_CLASS)
        try:
            self.encryption_algorithm = getattr(hashlib, settings.ENCRYPTION_ALGORITHM)
        except AttributeError:
            raise Exception(f"'{settings.ENCRYPTION_ALGORITHM}' is not a valid hashlib encryption algorithm.")

    def upload_metadata(self, metadata, storage_destination: str) -> dict:
        """
        Args:
            metadata: metadata to upload, has to contain logo:InMemoryUploadedFile
            storage_destination: pathstr / folder name. e.g.: "folder_1/folder_2/my_file.jpeg"

        Returns:
            metadata dict e.g.:
            {
                "description": "some description",
                "email": "some@email",
                "images": {
                    "logo": {
                        "content_type": "image/jpeg",
                        "small": {
                            "url": "https://some_bucket.s3.some_region.amazonaws.com/some_folder/logo_small.jpeg",
                        },
                        "medium": ...,
                    }
                },
                "metadata_hash": "ecda2de0cb7a7f293072a18ac088d5ce6595328e29d6174425e7949f7c2829da",
                "metadata_url": "https://some_bucket.s3.some_region.amazonaws.com/some_folder/metadata.json",
            }

        creates 3 files by resizing the logo to small, medium and large; size specified in envvar LOGO_SIZES.
        uploads the resized files using the file upload class' (provided via envvar FILE_UPLOAD_CLASS) upload_file
        method to a 'storage_destination', e.g. Dao.id.
        """
        # derive format from file extension
        logo = metadata.pop("logo")
        _format = logo.name.split(".")[-1]
        if _format == "jpg":
            _format = "jpeg"
        metadata = {**metadata, "images": {"logo": {"content_type": logo.content_type}}}
        with Image.open(logo) as img:
            for size_name, dimensions in settings.LOGO_SIZES.items():
                io = BytesIO()
                img.resize(dimensions).save(io, format=_format)
                io.seek(0)
                url = self.file_upload_class.upload_file(
                    file=io, storage_destination=f"{storage_destination}/logo_{size_name}.{_format}"
                )
                metadata["images"]["logo"][size_name] = {"url": url}

        encoded_metadata = json.dumps(metadata, indent=4).encode()
        io = BytesIO(encoded_metadata)
        io.seek(0)
        metadata["metadata_hash"] = self.encryption_algorithm(encoded_metadata).hexdigest()
        metadata["metadata_url"] = self.file_upload_class.upload_file(
            file=io, storage_destination=f"{storage_destination}/metadata.json"
        )
        return metadata


file_uploader = FileUploader()
