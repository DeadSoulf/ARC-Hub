# ARC Plugin (short name)

The actual ChatGPT plugin currently connected as "AI Remote Commander Local"
is NOT modified, so Asus access is preserved.

This is the **ARC** v0.2 manifest template, not a connected plugin.
Replace HTTPS hostname with the real VDS domain, deploy ARC Hub, verify
certificates and permissions, and then register a new private ChatGPT plugin.

The prototype supports independent user bearer credentials; each user must
configure their own connection/token. NEVER use an owner/admin bearer token
in a shared workspace plugin. For a single shared plugin with individual
sign-in, add standards-compliant OAuth 2.1 / OIDC to ARC Hub (authorization
server discovery, PKCE S256, issuer/audience validation and short-lived
access tokens), and update mcp.json auth from api_key to oauth.

Current routes: arc_devices, arc_permissions, arc_take_control,
arc_release_control, arc_call. Every computer operation requires device_id.
