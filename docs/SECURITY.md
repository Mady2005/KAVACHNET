# Security notes

- Passwords are Argon2 hashes; login errors do not reveal whether an account exists.
- JWTs are signed, short lived, and carry a unique identifier checked against the revocation table on protected API calls. The UI keeps bearer tokens in memory only; it can restore a session using the HttpOnly cookie after a page refresh. Logout invalidates that token server-side.
- All protected endpoints use backend dependencies; roles are not inferred from client input. ADMIN-only endpoints manage roles, registry metadata and audit visibility; event writes are limited to module operators and ADMIN.
- Authentication successes/failures, role changes, event writes and dataset changes are audited. Audit details must never contain passwords, tokens or secrets.
- New registrations are off by default. Local demo provisioning refuses non-development mode and is idempotent.
- Login applies a per-identity in-memory lockout after five failures in a 15-minute window. Process restarts clear it, so use a shared rate limiter or persistent lockout at the gateway before multi-worker or production exposure.
- Configure a strong `SECRET_KEY`, HTTPS, `COOKIE_SECURE=true`, restricted CORS and secret management before deployment.
- Session cookie is HttpOnly/SameSite=Lax. Cookie-authenticated state-changing requests require the separate CSRF cookie value in the `X-CSRF-Token` header; bearer-authenticated requests do not use that fallback.
- Account recovery, MFA, password policy enforcement and deployment hardening remain future work.
