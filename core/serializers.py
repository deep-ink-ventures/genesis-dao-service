from drf_extra_fields.fields import Base64ImageField
from rest_framework.fields import CharField, EmailField, IntegerField, URLField
from rest_framework.serializers import ModelSerializer, Serializer

from core import models


class StatsSerializer(Serializer):  # noqa
    dao_count = IntegerField(min_value=0)
    account_count = IntegerField(min_value=0)


class BalanceSerializer(Serializer):  # noqa
    free = IntegerField()
    reserved = IntegerField()
    misc_frozen = IntegerField()
    fee_frozen = IntegerField()


class AccountSerializerDetail(ModelSerializer):
    balance = BalanceSerializer(required=True)

    class Meta:
        model = models.Account
        fields = ("address", "balance")


class AccountSerializerList(ModelSerializer):
    class Meta:
        model = models.Account
        fields = ("address",)


class DaoSerializerDetail(ModelSerializer):
    owner_id = CharField(required=True)
    asset_id = IntegerField(source="asset.id", required=False)

    class Meta:
        model = models.Dao
        fields = ("id", "name", "owner_id", "asset_id", "metadata_url", "metadata_hash")


class DaoSerializerList(ModelSerializer):
    owner_id = CharField(required=True)

    class Meta:
        model = models.Dao
        fields = ("id", "name", "owner_id")


class MetadataSerializer(Serializer):  # noqa
    description = CharField(required=False)
    email = EmailField(required=False)
    logo = Base64ImageField(
        help_text=f"B64 encoded image string.\nAllowed image types are: {', '.join(Base64ImageField.ALLOWED_TYPES)}"
    )


class MetadataResponseSerializer(Serializer):  # noqa
    class MetadataSerializer(Serializer):  # noqa
        description = CharField(required=False)
        email = EmailField(required=False)

        class Meta:  # noqa
            ref_name = "ResponseMetadataSerializer"

        class ImagagesSerializer(Serializer):  # noqa
            class Meta:  # noqa
                ref_name = "ResponseImageSerializer"

            class LogoSerializer(Serializer):  # noqa
                class UrlSerializer(Serializer):  # noqa
                    url = URLField()

                content_type = CharField()
                small = UrlSerializer()
                medium = UrlSerializer()
                large = UrlSerializer()

            logo = LogoSerializer()

        images = ImagagesSerializer()

    metadata = MetadataSerializer()
    metadata_hash = CharField()
    metadata_url = URLField()


class AssetSerializer(ModelSerializer):
    id = IntegerField(min_value=0)
    dao_id = CharField(required=True)
    owner_id = CharField(required=True)
    total_supply = IntegerField(min_value=0)

    class Meta:
        model = models.Asset
        fields = ("id", "dao_id", "owner_id", "total_supply")


class AssetHoldingSerializer(ModelSerializer):
    asset_id = IntegerField(min_value=0)
    owner_id = CharField(required=True)
    balance = IntegerField(min_value=0)

    class Meta:
        model = models.AssetHolding
        fields = ("id", "asset_id", "owner_id", "balance")


class ProposalSerializer(ModelSerializer):
    class Meta:
        model = models.Proposal
        fields = ("id", "dao_id", "metadata", "metadata_url", "metadata_hash")


class ChallengeSerializer(Serializer):  # noqa
    challenge = CharField(required=True)
