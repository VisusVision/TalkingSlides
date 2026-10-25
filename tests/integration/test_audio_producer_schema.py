"""Inactive schema compatibility. These are not PostgreSQL authority tests."""

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
import pytest
from core.audio_producer_models import AudioProducerControl, AudioProducerAuthority

pytestmark = pytest.mark.django_db


def test_six_tables_start_empty_no_ordinary_relationship_cascades():
    models = [m for m in apps.get_models() if m.__name__.startswith("AudioProducer")]
    assert len(models) == 6
    for m in models:
        assert not m.objects.exists()
        for field in m._meta.fields:
            if field.is_relation:
                assert field.related_model in models


def test_missing_control_target_is_not_authority():
    with pytest.raises(IntegrityError), transaction.atomic():
        AudioProducerControl.objects.create()


def test_no_orm_rewrite_or_delete_shortcut():
    with pytest.raises(ValidationError):
        AudioProducerAuthority.objects.all().update(environment="fake")
    with pytest.raises(ValidationError):
        AudioProducerAuthority.objects.all().delete()
