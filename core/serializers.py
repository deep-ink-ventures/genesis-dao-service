from rest_framework.serializers import ModelSerializer

from core import models


class AccountSerializer(ModelSerializer):
    class Meta:
        model = models.Account
        fields = ("address",)


class DaoSerializer(ModelSerializer):
    class Meta:
        model = models.Dao
        fields = ("id", "name", "owner_id")


class AssetSerializer(ModelSerializer):
    class Meta:
        model = models.Asset
        fields = ("id", "dao_id", "owner_id", "total_supply")


class AssetHoldingSerializer(ModelSerializer):
    class Meta:
        model = models.AssetHolding
        fields = ("asset_id", "owner_id", "balance")
