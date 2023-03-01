from unittest.mock import Mock


def upload_file(file=None, storage_destination=None):
    return f"https://some_storage.some_region.com/{storage_destination}"


file_uploader_mock = Mock()
file_uploader_mock.upload_file.side_effect = upload_file
