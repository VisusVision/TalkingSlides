# Auth Regression Coverage

This note documents the current auth behavior that is intentionally locked by
regression tests. It does not propose or apply a production auth change.

## Current Model

- Frontend stores the DRF auth token in `localStorage` under `auth_token`.
- Frontend also stores cached user/provider state in `auth_user` and
  `auth_provider`.
- API requests use `Authorization: Token <token>`.
- Password login and Google login both return a Django REST Framework
  authtoken.
- Google redirect callback can deliver the token in the frontend URL fragment as
  `#auth_token=...`; the app then removes the fragment with `history.replaceState`.
- Logout posts to `/api/v1/auth/logout/`, deletes the server token when
  authenticated, and clears local frontend auth state.
- Media/playback tokens are a separate HMAC-signed URL token system with its own
  TTL and grant checks.

## Known Risks

- A successful XSS can read `localStorage.auth_token`.
- URL-fragment token delivery reduces server log exposure, but still exposes the
  token to browser/runtime surfaces before cleanup.
- DRF authtoken does not provide access/refresh separation or built-in token TTL
  in the current code path.
- Frontend cached auth state is useful for UX but must not be treated as backend
  authorization.

## Coverage Added

- Frontend token persistence in `localStorage`.
- Frontend logout cleanup for token, provider, and cached user state.
- Frontend cleanup when `/auth/me/` rejects a stored token.
- Redirect hash parsing and `history.replaceState` cleanup.
- Missing/blank redirect hash token handling.
- Backend logout revocation of old tokens.
- Backend denial for deleted/revoked tokens.
- Backend unauthenticated `/auth/me/` behavior.
- Login throttle configuration regression.
- Repeated logout with an old token failing closed.
- Current password login token reuse behavior.

## Future Hardening Roadmap

- Replace URL-fragment token delivery with a one-time code or backend-set
  httpOnly cookie flow.
- Move browser auth away from readable `localStorage` tokens.
- Introduce short-lived access tokens, refresh rotation, and server-side revoke
  visibility if token auth remains header-based.
- Add CSRF, SameSite, Secure, and credentialed CORS tests if auth moves to
  cookies.
- Add CSP/reporting and XSS regression checks around auth-bearing pages.
