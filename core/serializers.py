from drf_extra_fields.fields import Base64ImageField
from rest_framework.fields import CharField, EmailField
from rest_framework.serializers import ModelSerializer, Serializer

from core import models


class AccountSerializer(ModelSerializer):
    class Meta:
        model = models.Account
        fields = ("address",)


class DaoSerializerDetail(ModelSerializer):
    class Meta:
        model = models.Dao
        fields = ("id", "name", "owner_id", "metadata_url", "metadata_hash")


class DaoSerializerList(ModelSerializer):
    class Meta:
        model = models.Dao
        fields = ("id", "name", "owner_id")


class MetadataSerializer(Serializer):
    description = CharField(allow_blank=True, allow_null=True)
    email = EmailField(allow_blank=True, allow_null=True)
    logo = Base64ImageField()

    class Meta:
        fields = ("description", "email", "logo")

    def create(self, validated_data):
        raise NotImplementedError

    def update(self, instance, validated_data):
        raise NotImplementedError


class AssetSerializer(ModelSerializer):
    class Meta:
        model = models.Asset
        fields = ("id", "dao_id", "owner_id", "total_supply")


class AssetHoldingSerializer(ModelSerializer):
    class Meta:
        model = models.AssetHolding
        fields = ("asset_id", "owner_id", "balance")
