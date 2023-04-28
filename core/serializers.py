from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from drf_yasg.utils import swagger_serializer_method
from rest_framework.fields import (
    CharField,
    DateTimeField,
    EmailField,
    IntegerField,
    SerializerMethodField,
    URLField,
)
from rest_framework.serializers import ModelSerializer, Serializer, ValidationError

from core import models
from core.utils import B64ImageField


class StatsSerializer(Serializer):  # noqa
    dao_count = IntegerField(min_value=0)
    account_count = IntegerField(min_value=0)
    proposal_count = IntegerField(min_value=0)
    vote_count = IntegerField(min_value=0)


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
    proposal_duration = SerializerMethodField(help_text="Proposal duration in seconds.")

    class Meta:
        model = models.Dao
        fields = (
            "id",
            "name",
            "creator_id",
            "owner_id",
            "asset_id",
            "proposal_duration",
            "setup_complete",
            "metadata",
            "metadata_url",
            "metadata_hash",
        )

    @staticmethod
    @swagger_serializer_method(IntegerField)
    def get_proposal_duration(dao: models.Dao):
        try:
            return dao.governance.proposal_duration * settings.BLOCK_CREATION_INTERVAL
        except ObjectDoesNotExist:
            return None


class AddDaoMetadataSerializer(Serializer):  # noqa
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


class DaoMetadataResponseSerializer(Serializer):  # noqa
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


class VotesSerializer(Serializer):  # noqa
    pro = IntegerField(min_value=0)
    contra = IntegerField(min_value=0)
    abstained = IntegerField(min_value=0)
    total = IntegerField(min_value=0)

    def to_representation(self, instance):
        pro, contra, abstained, total = 0, 0, 0, 0
        for vote in instance.instance.votes.all():
            total += vote.voting_power
            match vote.in_favor:
                case True:
                    pro += vote.voting_power
                case False:
                    contra += vote.voting_power
                case _:
                    abstained += vote.voting_power
        return {"pro": pro, "contra": contra, "abstained": abstained, "total": total}


class ProposalSerializer(ModelSerializer):
    votes = VotesSerializer()
    ends_at = DateTimeField(help_text="Time proposal ends at in UTC.")

    class Meta:
        model = models.Proposal
        fields = (
            "id",
            "dao_id",
            "creator_id",
            "status",
            "fault",
            "votes",
            "metadata",
            "metadata_url",
            "metadata_hash",
            "ends_at",
        )


class AddProposalMetadataSerializer(Serializer):  # noqa
    title = CharField(max_length=64)
    description = CharField(max_length=512)
    url = URLField()


class ProposalMetadataResponseSerialzier(Serializer):  # noqa
    metadata = AddProposalMetadataSerializer()
    metadata_hash = CharField()
    metadata_url = URLField()


class ReportFaultedSerializer(ModelSerializer):
    proposal_id = CharField()
    reason = CharField(max_length=1024)

    class Meta:
        model = models.ProposalReport
        fields = ("proposal_id", "reason")

    def create(self, validated_data):
        return models.ProposalReport.objects.create(**validated_data)


class ChallengeSerializer(Serializer):  # noqa
    challenge = CharField(required=True, help_text=f"Valid for {settings.CHALLENGE_LIFETIME}s.")
