import logging

from celery import shared_task

from core import models

logger = logging.getLogger("alerts")


@shared_task()
def update_dao_metadata(dao_metadata: dict):
    """
    Args:
        dao_metadata: DAO metadata. format: {dao_id: {"metadata_url": metadata_url, "metadata_hash": metadata_hash}}

    Returns:
        None

    fetches all DAOs identified by dao_id in dao_metadata from db.
    for all fetched DAOs:
        - checks if the metadata_hash differs from the metadata_hash provided to the task (dao_metadata)
        - if so download metadata from provided url and updates DAO
    """
    from core.file_handling.file_handler import HashMismatchException, file_handler

    daos = set(models.Dao.objects.filter(id__in=dao_metadata.keys()))
    # update DAOs w/ differing metadata_hash
    for dao in (daos_to_update := {dao for dao in daos if dao.metadata_hash != dao_metadata[dao.id]["metadata_hash"]}):
        metadata_url = dao_metadata[dao.id]["metadata_url"]
        metadata_hash = dao_metadata[dao.id]["metadata_hash"]
        dao.metadata_url = metadata_url
        dao.metadata_hash = metadata_hash
        try:
            dao.metadata = file_handler.download_metadata(url=metadata_url, metadata_hash=metadata_hash)
        except HashMismatchException:
            logger.error("Hash mismatch while fetching DAO metadata from provided url.")
        except Exception:  # noqa
            logger.exception("Unexpected error while fetching DAO metadata from provided url.")

    if daos_to_update:
        models.Dao.objects.bulk_update(daos_to_update, fields=["metadata", "metadata_url", "metadata_hash"])
