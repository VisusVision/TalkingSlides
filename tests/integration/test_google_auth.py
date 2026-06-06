# pyright: reportMissingImports=false

import os
import sys
import uuid
from pathlib import Path

import django
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
API_ROOT = REPO_ROOT / 'services' / 'api'
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test.utils import override_settings
from rest_framework.authtoken.models import Token
from rest_framework.test import APIRequestFactory, force_authenticate

from core import views  # noqa: E402
from core.models import UserProfile  # noqa: E402


def _valid_google_payload(email='new.student@example.com', given='New', family='Student'):
    return {
        'email': email,
        'email_verified': True,
        'given_name': given,
        'family_name': family,
    }


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=False, GOOGLE_CLIENT_ID='')
def test_google_auth_config_missing_is_reported_disabled():
    factory = APIRequestFactory()
    request = factory.get('/api/v1/auth/providers/')

    response = views.AuthProvidersView.as_view()(request)

    assert response.status_code == 200
    assert response.data['google']['enabled'] is False
    assert response.data['google']['available'] is False
    assert response.data['google']['client_id'] == ''


@pytest.mark.django_db
@override_settings(
    GOOGLE_AUTH_ENABLED=True,
    GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com',
    GOOGLE_REDIRECT_URI='http://localhost:5173',
)
def test_google_auth_config_enabled_exposes_public_values():
    factory = APIRequestFactory()
    request = factory.get('/api/v1/auth/providers/')

    response = views.AuthProvidersView.as_view()(request)

    assert response.status_code == 200
    assert response.data['google']['enabled'] is True
    assert response.data['google']['available'] is True
    assert response.data['google']['client_id'] == 'google-client-id.apps.googleusercontent.com'
    assert response.data['google']['redirect_uri'] == 'http://localhost:5173'


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=True, GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com')
def test_google_login_creates_new_user_on_first_sign_in(monkeypatch):
    factory = APIRequestFactory()
    unique_email = f"google-new-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(
        views,
        '_verify_google_credential',
        lambda credential: _valid_google_payload(email=unique_email),
    )

    request = factory.post('/api/v1/auth/google/', {'credential': 'valid-token'}, format='json')
    response = views.GoogleLoginView.as_view()(request)

    assert response.status_code == 200
    assert response.data['created'] is True
    user = User.objects.get(email=unique_email)
    assert user.first_name == 'New'
    assert user.last_name == 'Student'
    assert UserProfile.objects.get(user=user).role == 'student'
    assert response.data['user']['email'] == unique_email
    assert response.data['user']['auth_provider'] == 'google'


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=True, GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com')
def test_google_login_uses_existing_user_by_email(monkeypatch):
    unique_suffix = uuid.uuid4().hex[:8]
    user = User.objects.create_user(
        username=f'existing-teacher-{unique_suffix}',
        email=f'teacher-{unique_suffix}@example.com',
        password='secret123',
        first_name='Existing',
        last_name='Teacher',
        is_staff=True,
    )
    UserProfile.objects.create(user=user, role='teacher')

    monkeypatch.setattr(
        views,
        '_verify_google_credential',
        lambda credential: _valid_google_payload(email=f'teacher-{unique_suffix}@example.com', given='Updated', family='Teacher'),
    )

    factory = APIRequestFactory()
    request = factory.post('/api/v1/auth/google/', {'credential': 'valid-token'}, format='json')
    response = views.GoogleLoginView.as_view()(request)

    assert response.status_code == 200
    assert response.data['created'] is False
    user.refresh_from_db()
    assert user.username == f'existing-teacher-{unique_suffix}'
    assert user.is_staff is True
    assert user.profile.role == 'teacher'
    assert user.first_name == 'Updated'


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=True, GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com')
def test_google_provider_token_is_valid_for_me_view(monkeypatch):
    unique_email = f"google-session-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(views, '_verify_google_credential', lambda credential: _valid_google_payload(email=unique_email))

    factory = APIRequestFactory()
    login_request = factory.post('/api/v1/auth/google/', {'credential': 'valid-token'}, format='json')
    login_response = views.GoogleLoginView.as_view()(login_request)

    token = Token.objects.get(key=login_response.data['token'])
    me_request = factory.get('/api/v1/auth/me/')
    force_authenticate(me_request, user=token.user, token=token)

    me_response = views.MeView.as_view()(me_request)

    assert me_response.status_code == 200
    assert me_response.data['email'] == unique_email
    assert me_response.data['auth_provider'] == 'google'


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=True, GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com')
def test_google_logout_deletes_token_and_provider_state(monkeypatch):
    unique_email = f"google-logout-{uuid.uuid4().hex[:8]}@example.com"
    monkeypatch.setattr(views, '_verify_google_credential', lambda credential: _valid_google_payload(email=unique_email))

    factory = APIRequestFactory()
    login_request = factory.post('/api/v1/auth/google/', {'credential': 'valid-token'}, format='json')
    login_response = views.GoogleLoginView.as_view()(login_request)

    token_key = login_response.data['token']
    token = Token.objects.get(key=token_key)
    cache_key = views._token_provider_cache_key(token_key)
    assert cache.get(cache_key) == 'google'

    logout_request = factory.post('/api/v1/auth/logout/', {}, format='json')
    force_authenticate(logout_request, user=token.user, token=token)
    logout_response = views.LogoutView.as_view()(logout_request)

    assert logout_response.status_code == 200
    assert not Token.objects.filter(key=token_key).exists()
    assert cache.get(cache_key) is None


@pytest.mark.django_db
@override_settings(
    GOOGLE_AUTH_ENABLED=True,
    GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com',
    GOOGLE_CLIENT_SECRET='secret',
    GOOGLE_REDIRECT_URI='http://localhost:8000/api/v1/auth/google/redirect/callback/',
)
def test_google_redirect_start_returns_authorization_url():
    factory = APIRequestFactory()
    request = factory.get('/api/v1/auth/google/redirect/start/')

    response = views.GoogleRedirectStartView.as_view()(request)

    assert response.status_code == 200
    assert 'authorization_url' in response.data
    assert 'accounts.google.com' in response.data['authorization_url']
    assert response.data['state']


@pytest.mark.django_db
@override_settings(
    GOOGLE_AUTH_ENABLED=True,
    GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com',
    GOOGLE_CLIENT_SECRET='',
    GOOGLE_REDIRECT_URI='http://localhost:8000/api/v1/auth/google/redirect/callback/',
)
def test_google_redirect_start_returns_graceful_fallback_when_config_missing():
    factory = APIRequestFactory()
    request = factory.get('/api/v1/auth/google/redirect/start/')

    response = views.GoogleRedirectStartView.as_view()(request)

    assert response.status_code == 503
    assert response.data['error'] == 'Google redirect sign-in is not configured.'


@pytest.mark.django_db
@override_settings(
    GOOGLE_AUTH_ENABLED=True,
    GOOGLE_CLIENT_ID='google-client-id.apps.googleusercontent.com',
    GOOGLE_CLIENT_SECRET='secret',
    GOOGLE_REDIRECT_URI='http://localhost:8000/api/v1/auth/google/redirect/callback/',
    GOOGLE_REDIRECT_SUCCESS_URL='',
)
def test_google_redirect_callback_exchanges_code_and_returns_token_json(monkeypatch):
    factory = APIRequestFactory()
    unique_email = f"google-redirect-{uuid.uuid4().hex[:8]}@example.com"

    start_request = factory.get('/api/v1/auth/google/redirect/start/')
    start_response = views.GoogleRedirectStartView.as_view()(start_request)
    state = start_response.data['state']

    monkeypatch.setattr(views, '_exchange_google_oauth_code', lambda code: {'id_token': 'google-id-token'})
    monkeypatch.setattr(views, '_verify_google_credential', lambda credential: _valid_google_payload(email=unique_email))

    callback_request = factory.get(f'/api/v1/auth/google/redirect/callback/?code=oauth-code&state={state}')
    callback_response = views.GoogleRedirectCallbackView.as_view()(callback_request)

    assert callback_response.status_code == 200
    assert callback_response.data['provider'] == 'google'
    assert callback_response.data['user']['auth_provider'] == 'google'
    assert Token.objects.filter(key=callback_response.data['token']).exists()


@pytest.mark.django_db
@override_settings(GOOGLE_AUTH_ENABLED=False, GOOGLE_CLIENT_ID='')
def test_google_login_returns_graceful_fallback_when_disabled():
    factory = APIRequestFactory()
    request = factory.post('/api/v1/auth/google/', {'credential': 'valid-token'}, format='json')

    response = views.GoogleLoginView.as_view()(request)

    assert response.status_code == 503
    assert response.data['error'] == 'Google sign-in is not configured.'
