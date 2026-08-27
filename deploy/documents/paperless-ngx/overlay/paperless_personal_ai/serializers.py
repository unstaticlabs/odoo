from rest_framework import serializers

from paperless_personal_ai.service import APPROVED_MODELS, PROVIDER

ONLY_GEMINI_MESSAGE = "Only Gemini is supported."
PINNED_MODEL_MESSAGE = "Select a pinned Gemini model."


class PersonalAIUpdateSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(
        choices=(PROVIDER,),
        required=False,
    )
    model_name = serializers.ChoiceField(
        choices=APPROVED_MODELS,
        required=False,
    )
    metadata_suggestions_enabled = serializers.BooleanField(required=False)
    document_chat_enabled = serializers.BooleanField(required=False)
    api_key = serializers.CharField(
        write_only=True,
        required=False,
        allow_blank=False,
        max_length=512,
        trim_whitespace=False,
    )

    def validate_provider(self, value: str) -> str:
        if value != PROVIDER:
            raise serializers.ValidationError(ONLY_GEMINI_MESSAGE)
        return value

    def validate_model_name(self, value: str) -> str:
        if "latest" in value.casefold():
            raise serializers.ValidationError(PINNED_MODEL_MESSAGE)
        return value


class PersonalAIProfileSerializer(serializers.Serializer):
    provider = serializers.CharField(read_only=True)
    approved_models = serializers.ListField(
        child=serializers.CharField(),
        read_only=True,
    )
    model_name = serializers.CharField(read_only=True)
    metadata_suggestions_enabled = serializers.BooleanField(read_only=True)
    document_chat_enabled = serializers.BooleanField(read_only=True)
    api_key_configured = serializers.BooleanField(read_only=True)
    credential_revision = serializers.IntegerField(read_only=True)
    last_tested_at = serializers.DateTimeField(read_only=True, allow_null=True)
    privacy_disclosure = serializers.CharField(read_only=True)
