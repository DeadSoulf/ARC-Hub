# Security policy

ARC 0.2 is an experimental remote-control product and is **not production-hardened**. Do not publish a live service before validating authentication, TLS, per-device grants, logging and secrets management.

Avoid posting passwords, API keys, private tokens, personal files or security vulnerabilities in public GitHub issues. Revoke and rotate any accidentally disclosed credentials immediately.

Separate administrator, user and device credentials. Use a proper OAuth/OIDC identity provider for a shared ChatGPT plugin instead of reusing one privileged bearer token.

Keep control sessions visible and stoppable by the local machine owner.
