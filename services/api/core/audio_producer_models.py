"""Six empty P1 ledger tables. No producer/publisher runtime integration.

External application IDs are historical scalar references, intentionally not
cascading foreign keys. PostgreSQL guards and operator role provisioning, not
these ORM conveniences, establish the independent writer boundary.
"""

import uuid
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from core.visual_design.schemas import canonical_json, content_hash


class AppendOnlyQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("Producer history is immutable")

    def delete(self):
        raise ValidationError("Producer history is immutable")


class Fact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    payload = models.JSONField()
    payload_hash = models.CharField(max_length=71)
    canonical_payload = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    objects = AppendOnlyQuerySet.as_manager()

    class Meta:
        abstract = True

    def clean(self):
        from core.visual_design.segment_contracts import _bounded

        _bounded(self.payload)
        if self.canonical_payload != canonical_json(self.payload):
            raise ValidationError("Canonical payload differs")
        if self.payload_hash != content_hash(
            self.payload["schema_version"], self.payload
        ):
            raise ValidationError("Payload digest differs")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Producer history is immutable")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Producer history is immutable")


class AudioProducerAuthority(Fact):
    issuer_id = models.CharField(max_length=128)
    key_id = models.CharField(max_length=128)
    purpose = models.CharField(max_length=64)
    environment = models.CharField(max_length=128)
    public_key = models.CharField(max_length=43)
    image_hash = models.CharField(max_length=71)
    profile_hash = models.CharField(max_length=71)
    not_before_ms = models.PositiveBigIntegerField()
    expires_ms = models.PositiveBigIntegerField()
    signed_enrollment = models.TextField()

    class Meta:
        db_table = "audio_producer_authority"
        constraints = [
            models.CheckConstraint(
                condition=Q(expires_ms__gt=models.F("not_before_ms")),
                name="audio_authority_window",
            ),
            models.UniqueConstraint(
                fields=[
                    "issuer_id",
                    "key_id",
                    "purpose",
                    "environment",
                    "payload_hash",
                ],
                name="audio_issuer_grant_unique",
            ),
        ]


class AudioProducerExecution(Fact):
    owner_id = models.PositiveBigIntegerField()
    project_id = models.PositiveBigIntegerField()
    job_id = models.PositiveBigIntegerField()
    page_id = models.PositiveBigIntegerField(null=True)
    page_key = models.CharField(max_length=64)
    source_slide_index = models.PositiveIntegerField()
    split_index = models.PositiveIntegerField()
    planned_speech_hash = models.CharField(max_length=71)
    issuer = models.ForeignKey(AudioProducerAuthority, on_delete=models.PROTECT)

    class Meta:
        db_table = "audio_producer_execution"


class AudioProducerAttempt(Fact):
    execution = models.ForeignKey(AudioProducerExecution, on_delete=models.PROTECT)
    issuer = models.ForeignKey(AudioProducerAuthority, on_delete=models.PROTECT)
    issued_event = models.OneToOneField(
        "AudioProducerControlEvent", on_delete=models.PROTECT
    )
    generation = models.PositiveSmallIntegerField()
    nonce = models.UUIDField(unique=True)
    context_hash = models.CharField(max_length=71, unique=True)
    outcome = models.CharField(
        max_length=16, choices=[(x, x) for x in ["complete", "failed", "ineligible"]]
    )
    actual_speech_hash = models.CharField(max_length=71, null=True)
    signed_observation = models.TextField()

    class Meta:
        db_table = "audio_producer_attempt"
        constraints = [
            models.UniqueConstraint(
                fields=["execution", "generation"],
                name="audio_attempt_generation_unique",
            ),
            models.CheckConstraint(
                condition=Q(generation__gte=1, generation__lte=3),
                name="audio_attempt_bound",
            ),
        ]


class AudioProducerReceipt(Fact):
    execution = models.OneToOneField(AudioProducerExecution, on_delete=models.PROTECT)
    attempt = models.OneToOneField(AudioProducerAttempt, on_delete=models.PROTECT)
    issuer = models.ForeignKey(AudioProducerAuthority, on_delete=models.PROTECT)
    proof_hash = models.CharField(max_length=71)
    signed_receipt = models.TextField()

    class Meta:
        db_table = "audio_producer_receipt"


class AudioProducerControl(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    authority = models.OneToOneField(
        AudioProducerAuthority, on_delete=models.PROTECT, null=True
    )
    execution = models.OneToOneField(
        AudioProducerExecution, on_delete=models.PROTECT, null=True
    )
    receipt = models.OneToOneField(
        AudioProducerReceipt, on_delete=models.PROTECT, null=True
    )
    epoch = models.PositiveBigIntegerField(default=0)
    status = models.CharField(
        max_length=16,
        default="disabled",
        choices=[(x, x) for x in ["disabled", "active", "revoked", "retired_valid"]],
    )
    generation = models.PositiveSmallIntegerField(default=0)
    nonce = models.UUIDField(null=True)
    deadline = models.DateTimeField(null=True)
    accepted_receipt = models.OneToOneField(
        AudioProducerReceipt,
        on_delete=models.PROTECT,
        null=True,
        related_name="accepting_control",
    )
    head_event = models.OneToOneField(
        "AudioProducerControlEvent",
        on_delete=models.PROTECT,
        null=True,
        related_name="headed_control",
    )

    class Meta:
        db_table = "audio_producer_control"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(
                        authority__isnull=False,
                        execution__isnull=True,
                        receipt__isnull=True,
                    )
                    | Q(
                        authority__isnull=True,
                        execution__isnull=False,
                        receipt__isnull=True,
                    )
                    | Q(
                        authority__isnull=True,
                        execution__isnull=True,
                        receipt__isnull=False,
                    )
                ),
                name="audio_control_one_target",
            ),
            models.CheckConstraint(
                condition=Q(generation__lte=3), name="audio_control_generation_bound"
            ),
        ]


class AudioProducerControlEvent(Fact):
    control = models.ForeignKey(AudioProducerControl, on_delete=models.PROTECT)
    sequence = models.PositiveBigIntegerField()
    previous_hash = models.CharField(max_length=71, null=True)
    kind = models.CharField(max_length=24)
    execution = models.ForeignKey(
        AudioProducerExecution, on_delete=models.PROTECT, null=True
    )
    generation = models.PositiveSmallIntegerField(null=True)
    issued_nonce = models.UUIDField(null=True, unique=True)
    context_hash = models.CharField(max_length=71, null=True, unique=True)
    instruction = models.TextField()

    class Meta:
        db_table = "audio_producer_event"
        constraints = [
            models.UniqueConstraint(
                fields=["control", "sequence"], name="audio_event_sequence_unique"
            ),
            models.UniqueConstraint(
                fields=["execution", "generation"],
                name="audio_issued_generation_unique",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        kind="attempt_issued",
                        execution__isnull=False,
                        generation__gte=1,
                        generation__lte=3,
                        issued_nonce__isnull=False,
                        context_hash__isnull=False,
                    )
                    | (
                        ~Q(kind="attempt_issued")
                        & Q(
                            execution__isnull=True,
                            generation__isnull=True,
                            issued_nonce__isnull=True,
                            context_hash__isnull=True,
                        )
                    )
                ),
                name="audio_issued_event_shape",
            ),
        ]
