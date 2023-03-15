from django.db import models


class TimestampableMixin(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, editable=False)
    updated_at = models.DateTimeField(auto_now=True, editable=False)

    class Meta:
        abstract = True


class Account(TimestampableMixin):
    address = models.CharField(primary_key=True, max_length=128, unique=True, editable=False)

    class Meta:
        verbose_name = "Account"
        verbose_name_plural = "Accounts"


class Dao(TimestampableMixin):
    id = models.CharField(max_length=128, primary_key=True)
    name = models.CharField(max_length=128, null=True)
    owner = models.ForeignKey(Account, related_name="daos", on_delete=models.CASCADE)
    metadata = models.JSONField(null=True)
    metadata_url = models.CharField(max_length=256, null=True)
    metadata_hash = models.CharField(max_length=256, null=True)

    class Meta:
        verbose_name = "DAO"
        verbose_name_plural = "DAOs"


class Asset(TimestampableMixin):
    id = models.PositiveBigIntegerField(primary_key=True)
    total_supply = models.PositiveBigIntegerField()
    dao = models.OneToOneField(Dao, related_name="asset", on_delete=models.CASCADE)
    owner = models.ForeignKey(Account, related_name="assets", on_delete=models.CASCADE)

    class Meta:
        verbose_name = "Asset"
        verbose_name_plural = "Assets"


class AssetHolding(TimestampableMixin):
    asset = models.ForeignKey(Asset, related_name="holdings", on_delete=models.CASCADE)
    owner = models.ForeignKey(Account, related_name="holdings", on_delete=models.CASCADE)
    balance = models.PositiveBigIntegerField()

    class Meta:
        db_table = "core_asset_holding"
        unique_together = ("asset", "owner")
        verbose_name = "Asset Holding"
        verbose_name_plural = "Asset Holdings"

    def __str__(self):
        return f"{self.asset_id} | {self.owner_id} | {self.balance}"


class Block(TimestampableMixin):
    hash = models.CharField(primary_key=True, max_length=128, unique=True, editable=False)
    number = models.BigIntegerField(unique=True, editable=False)
    parent_hash = models.CharField(max_length=128, unique=True, editable=False, null=True)
    extrinsic_data = models.JSONField(default=dict)
    event_data = models.JSONField(default=dict)
    executed = models.BooleanField(default=False, db_index=True)

    class Meta:
        verbose_name = "Block"
        verbose_name_plural = "Blocks"

    def __str__(self):
        return f"{self.number}"
