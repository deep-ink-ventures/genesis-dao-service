from django.conf import settings
from rest_framework.fields import CharField, EmailField, IntegerField, URLField
from rest_framework.serializers import ModelSerializer, Serializer, ValidationError

from core import models
from core.utils import B64ImageField


class StatsSerializer(Serializer):  # noqa
    dao_count = IntegerField(min_value=0)
    account_count = IntegerField(min_value=0)


class ConfigSerializer(Serializer):  # noqa
    deposit_to_create_dao = IntegerField(
        min_value=0, help_text="Amount of native balance required to deposit when creating a DAO."
    )
    deposit_to_create_proposal = IntegerField(
        min_value=0, help_text="Amount of native balance required to deposit when creating a Proposal."
    )
    block_creation_interval = IntegerField(min_value=0, help_text="In seconds.")


class BalanceSerializer(Serializer):  # noqa
    free = IntegerField(min_value=0)
    reserved = IntegerField(min_value=0)
    misc_frozen = IntegerField(min_value=0)
    fee_frozen = IntegerField(min_value=0)


class AccountSerializerDetail(ModelSerializer):
    balance = BalanceSerializer(required=True)

    class Meta:
        model = models.Account
        fields = ("address", "balance")


class AccountSerializerList(ModelSerializer):
    class Meta:
        model = models.Account
        fields = ("address",)


class DaoSerializer(ModelSerializer):
    owner_id = CharField(required=True)
    asset_id = IntegerField(source="asset.id", required=False)

    class Meta:
        model = models.Dao
        fields = (
            "id",
            "name",
            "creator_id",
            "owner_id",
            "asset_id",
            "setup_complete",
            "metadata",
            "metadata_url",
            "metadata_hash",
        )


class MetadataSerializer(Serializer):  # noqa
    description_short = CharField(required=False)
    description_long = CharField(required=False)
    email = EmailField(required=False)
    logo = B64ImageField(
        help_text=f"B64 encoded image string.\nAllowed image types are: {', '.join(B64ImageField.ALLOWED_TYPES)}."
    )

    @staticmethod
    def validate_logo(logo):
        if logo.size > settings.MAX_LOGO_SIZE:
            raise ValidationError(f"The uploaded file is too big. Max size: {settings.MAX_LOGO_SIZE / 1_000_000} mb.")
        return logo


class MetadataResponseSerializer(Serializer):  # noqa
    class MetadataSerializer(Serializer):  # noqa
        description_short = CharField(required=False)
        description_long = CharField(required=False)
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
    challenge = CharField(required=True, help_text=f"Valid for {settings.CHALLENGE_LIFETIME}s.")
