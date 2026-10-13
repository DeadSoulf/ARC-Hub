#!/usr/bin/env python3
"""Optional local-only HTTP MCP endpoint for a user-approved outbound tunnel.

No central VDS; binding is hard-coded to 127.0.0.1.
For truly network-listener-free operation, use arc_stdio_full.py instead.
"""
import argparse
import os
from pathlib import Path

from arc_full_ops import FullAgent
from arc_local import LocalMCP, TOKEN_FILE, token_from_disk


class FullLocalMCP(LocalMCP):
    def __init__(self, address, token, agent):
        super().__init__(address, token, screen=False, apps=False, user_input=False)
        self.agent = agent

    def tools(self):
        return self.agent.tools()

    def call_tool(self, name, args):
        return self.agent.call(name, args)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--allow-root", type=Path, action="append")
    parser.add_argument("--enable-screen", action="store_true")
    parser.add_argument("--enable-apps", action="store_true")
    parser.add_argument("--enable-input", action="store_true")
    parser.add_argument("--allow-write", action="store_true")
    parser.add_argument("--allow-shell", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Windows only")
    if not 1024 <= args.port <= 65535:
        parser.error("Invalid local port")
    agent = FullAgent(args.allow_root or [Path.home()], screen=args.enable_screen,
                      apps=args.enable_apps, user_input=args.enable_input,
                      write=args.allow_write, shell=args.allow_shell)
    if args.self_test:
        print("FULL_LOCAL_HTTP_CONFIG_OK; tools=" + ",".join(t["name"] for t in agent.tools()))
        return
    token = token_from_disk(TOKEN_FILE)
    server = FullLocalMCP(("127.0.0.1", args.port), token, agent)
    print("AI Remote Commander Local HTTP (loopback only).")
    print("Endpoint: http://127.0.0.1:%d/mcp" % args.port)
    print("Token file: " + str(TOKEN_FILE))
    print("No central server or VDS. Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
