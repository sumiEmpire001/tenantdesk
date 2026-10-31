import argparse
import json
import os
from .desk import Desk
from .server import make_server


def main():
    parser = argparse.ArgumentParser(description="Local multi-tenant support API")
    parser.add_argument("--db", default="desk.db")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-tenant")
    create.add_argument("tenant")
    create.add_argument("admin")
    member = commands.add_parser("add-member")
    member.add_argument("name")
    member.add_argument("role", choices=["admin", "agent", "customer"])
    serve = commands.add_parser("serve")
    serve.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        desk = Desk(args.db)
        if args.command == "create-tenant":
            print(json.dumps(desk.create_tenant(args.tenant, args.admin)))
        elif args.command == "add-member":
            print(json.dumps(desk.add_member(os.environ.get("TENANTDESK_ADMIN_KEY", ""), args.name, args.role)))
        else:
            server = make_server(desk, args.port)
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
    except (ValueError, OSError, PermissionError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
