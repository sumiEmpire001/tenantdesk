import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit, parse_qs
from .desk import Conflict


def make_server(desk, port=8080):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def reply(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            self.dispatch()
        def do_POST(self):
            self.dispatch()
        def dispatch(self):
            try:
                auth = self.headers.get("Authorization", "")
                key = auth[7:] if auth.startswith("Bearer ") else ""
                parts = urlsplit(self.path)
                path = parts.path.strip("/").split("/")
                query = parse_qs(parts.query)
                value = {}
                if self.command == "POST":
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 16384 or self.headers.get("Transfer-Encoding"):
                        self.reply(413, {"error": "A 1..16384 byte JSON body with Content-Length is required"})
                        return
                    self.connection.settimeout(5)
                    value = json.loads(self.rfile.read(length))
                    if not isinstance(value, dict):
                        raise ValueError("Expected JSON object")
                if path == ["tickets"] and self.command == "GET":
                    self.reply(200, {"tickets": desk.tickets(key, query.get("q", [""])[0], limit=int(query.get("limit", [50])[0]), offset=int(query.get("offset", [0])[0]))})
                elif path == ["tickets"] and self.command == "POST":
                    self.reply(201, desk.create_ticket(key, **value))
                elif len(path) == 2 and path[0] == "tickets" and self.command == "GET":
                    self.reply(200, desk.get(key, path[1]))
                elif len(path) == 3 and path[0] == "tickets" and self.command == "POST" and path[2] == "comments":
                    desk.comment(key, path[1], **value)
                    self.reply(201, {"added": True})
                elif len(path) == 3 and path[0] == "tickets" and self.command == "POST" and path[2] == "mutate":
                    self.reply(200, desk.mutate(key, path[1], **value))
                else:
                    self.reply(404, {"error": "Route not found"})
            except PermissionError:
                self.reply(403, {"error": "Resource unavailable or permission denied"})
            except Conflict as exc:
                self.reply(409, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self.reply(400, {"error": str(exc)})
            except TimeoutError:
                self.reply(408, {"error": "Request body timed out"})
            except Exception:
                self.reply(500, {"error": "Internal error"})
    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
