from rest_framework import serializers

from core.models import (
    DigitalTwin,
    DigitalTwinConsentSession,
    DigitalTwinRender,
    DigitalTwinTrainingRun,
)


class DigitalTwinConsentSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalTwinConsentSession
        exclude = ["challenge_nonce_hash"]
        read_only_fields = [field.name for field in model._meta.fields]


class DigitalTwinTrainingRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalTwinTrainingRun
        fields = "__all__"
        read_only_fields = [field.name for field in model._meta.fields]


class DigitalTwinRenderSerializer(serializers.ModelSerializer):
    class Meta:
        model = DigitalTwinRender
        fields = "__all__"
        read_only_fields = [field.name for field in model._meta.fields]


class DigitalTwinSerializer(serializers.ModelSerializer):
    latest_training_run = serializers.SerializerMethodField()

    class Meta:
        model = DigitalTwin
        fields = [
            "id",
            "display_name",
            "status",
            "capabilities",
            "locale",
            "consent_status",
            "consent_decision",
            "reference_analysis",
            "identity_package",
            "voice_package",
            "motion_style_package",
            "look_packages",
            "model_versions",
            "failure_code",
            "failure_message",
            "revoked_at",
            "created_at",
            "updated_at",
            "latest_training_run",
        ]
        read_only_fields = fields

    def get_latest_training_run(self, obj):
        run = obj.training_runs.order_by("-created_at").first()
        return DigitalTwinTrainingRunSerializer(run).data if run else None
