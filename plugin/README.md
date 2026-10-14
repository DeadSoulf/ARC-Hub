# ARC ChatGPT plugin — OAuth 2.1 PKCE

The private ARC plugin connects to https://vpn.homeaicore.ru/mcp using
**OAuth Dynamic Client Registration (DCR)**, not a shared API key.

The HUB must first be updated to a version with arc_oauth.py and
ARC_PUBLIC_URL=https://vpn.homeaicore.ru in /etc/arc/oauth.env.

1. Confirm GET /.well-known/oauth-protected-resource and
   GET /.well-known/oauth-authorization-server respond with metadata.
2. Update the private ARC plugin with the OAuth MCP config.
3. Reconnect ARC in ChatGPT. You will be redirected to ARC Hub's own sign-in
   page, where you use a personal ARC account and explicitly approve ChatGPT.
4. Grant that account only the intended device-specific scopes in ARC Web.
5. For Asus Notepad, grant "apps.launch" in ARC Web (user and device)
   AND enable "apps" in the Windows agent's local permissions.
6. Test arc_devices, arc_permissions, arc_take_control, arc_call using
   open_notepad or open_application, and finally arc_release_control.
7. Keep Desktop Commander until the entire MCP path is verified end to end.

No static owner or device bearer token belongs in plugin files or git.
Authorization codes are single-use and bound to the S256 challenge.
Refresh tokens are stored hashed and rotated; access tokens are short-lived.
The HUB validates the user, target resource, device grants and control lease.

The Web UI may ask for your personal ARC token during initial OAuth consent.
Do not paste this token into a chat; enter it only on your verified
https://vpn.homeaicore.ru site over HTTPS.
