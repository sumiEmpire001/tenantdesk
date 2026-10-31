import concurrent.futures
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from tenantdesk import Desk, Conflict
from tenantdesk.server import make_server


class DeskTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "desk.db"
        self.desk = Desk(self.path)
        self.admin = self.desk.create_tenant("one", "admin")
        self.agent = self.desk.add_member(self.admin["api_key"], "agent", "agent")
        self.customer = self.desk.add_member(self.admin["api_key"], "customer", "customer")
        self.second = self.desk.add_member(self.admin["api_key"], "second", "customer")
        self.other = self.desk.create_tenant("two", "admin")
        self.ticket = self.desk.create_ticket(self.customer["api_key"], "Synthetic title", "Synthetic body")

    def test_cross_tenant_ticket_read_list_and_write_are_denied(self):
        self.assertEqual(self.desk.tickets(self.other["api_key"]), [])
        with self.assertRaises(PermissionError):
            self.desk.get(self.other["api_key"], self.ticket["id"])
        with self.assertRaises(PermissionError):
            self.desk.comment(self.other["api_key"], self.ticket["id"], "not allowed")

    def test_customers_only_see_own_tickets_but_agents_see_tenant(self):
        self.assertEqual(self.desk.tickets(self.second["api_key"]), [])
        with self.assertRaises(PermissionError):
            self.desk.get(self.second["api_key"], self.ticket["id"])
        self.assertEqual(len(self.desk.tickets(self.agent["api_key"])), 1)

    def test_admin_assigns_active_agent_in_same_tenant(self):
        result = self.desk.mutate(self.admin["api_key"], self.ticket["id"], expected_revision=0, assignee=self.agent["id"])
        self.assertEqual(result["assignee"], self.agent["id"])
        with self.assertRaises(ValueError):
            self.desk.mutate(self.admin["api_key"], self.ticket["id"], expected_revision=1, assignee=self.other["id"])

    def test_agent_and_customer_cannot_assign_or_provision_members(self):
        for person in (self.agent, self.customer):
            with self.assertRaises(PermissionError):
                self.desk.mutate(person["api_key"], self.ticket["id"], expected_revision=0, assignee=self.agent["id"])
            with self.assertRaises(PermissionError):
                self.desk.add_member(person["api_key"], "intruder", "admin")

    def test_comments_are_ordered_and_advance_revision(self):
        self.desk.comment(self.customer["api_key"], self.ticket["id"], "First")
        self.desk.comment(self.agent["api_key"], self.ticket["id"], "Second")
        result = self.desk.get(self.customer["api_key"], self.ticket["id"])
        self.assertEqual([c["body"] for c in result["comments"]], ["First", "Second"])
        self.assertEqual(result["revision"], 2)

    def test_customer_can_close_resolved_ticket_but_not_resolve_it(self):
        with self.assertRaises(PermissionError):
            self.desk.mutate(self.customer["api_key"], self.ticket["id"], expected_revision=0, state="resolved")
        self.desk.mutate(self.agent["api_key"], self.ticket["id"], expected_revision=0, state="resolved")
        self.desk.mutate(self.customer["api_key"], self.ticket["id"], expected_revision=1, state="closed")
        with self.assertRaises(PermissionError):
            self.desk.mutate(self.agent["api_key"], self.ticket["id"], expected_revision=2, state="open")
        self.desk.mutate(self.admin["api_key"], self.ticket["id"], expected_revision=2, state="open")

    def test_optimistic_race_has_one_winner(self):
        def mutate(state):
            try:
                return self.desk.mutate(self.agent["api_key"], self.ticket["id"], expected_revision=0, state=state)
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(mutate, ("resolved", "in_progress")))
        self.assertEqual(sum(r is not None for r in result), 1)

    def test_revocation_and_last_admin_protection(self):
        self.desk.disable_member(self.admin["api_key"], self.agent["id"])
        with self.assertRaises(PermissionError):
            self.desk.tickets(self.agent["api_key"])
        with self.assertRaises(ValueError):
            self.desk.disable_member(self.admin["api_key"], self.admin["id"])

    def test_audit_contains_no_api_keys_and_is_tenant_scoped(self):
        self.desk.comment(self.agent["api_key"], self.ticket["id"], "example")
        audit = self.desk.audit(self.customer["api_key"], self.ticket["id"])
        self.assertEqual([row["event"] for row in audit], ["created", "commented"])
        self.assertNotIn(self.agent["api_key"], json.dumps(audit))
        with self.assertRaises(PermissionError):
            self.desk.audit(self.other["api_key"], self.ticket["id"])

    def test_literal_search_pagination_and_restart(self):
        self.desk.create_ticket(self.customer["api_key"], "Percent % demo", "body")
        self.assertEqual(len(self.desk.tickets(self.agent["api_key"], "%")), 1)
        self.assertEqual(len(self.desk.tickets(self.agent["api_key"], limit=1, offset=1)), 1)
        self.assertEqual(Desk(self.path).get(self.customer["api_key"], self.ticket["id"])["title"], "Synthetic title")

    def test_invalid_input_and_closed_comments_do_not_change_state(self):
        with self.assertRaises(ValueError):
            self.desk.comment(self.customer["api_key"], self.ticket["id"], "")
        with self.assertRaises(ValueError):
            self.desk.mutate(self.agent["api_key"], self.ticket["id"], expected_revision=True, state="resolved")
        self.assertEqual(self.desk.get(self.agent["api_key"], self.ticket["id"])["revision"], 0)
        self.desk.mutate(self.agent["api_key"], self.ticket["id"], expected_revision=0, state="resolved")
        self.desk.mutate(self.customer["api_key"], self.ticket["id"], expected_revision=1, state="closed")
        with self.assertRaises(ValueError):
            self.desk.comment(self.customer["api_key"], self.ticket["id"], "late comment")
        self.assertEqual(self.desk.get(self.agent["api_key"], self.ticket["id"])["revision"], 2)

    def test_real_http_boundary_enforces_auth_and_tenant(self):
        server = make_server(self.desk, 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(path, key, body=None):
            req = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}", data=None if body is None else json.dumps(body).encode(), headers={"Authorization": "Bearer " + key})
            try:
                with urllib.request.urlopen(req, timeout=3) as response:
                    return response.status, json.loads(response.read())
            except urllib.error.HTTPError as error:
                return error.code, json.loads(error.read())
        try:
            self.assertEqual(request("/tickets", "invalid")[0], 403)
            self.assertEqual(request("/tickets/" + self.ticket["id"], self.other["api_key"])[0], 403)
            code, result = request("/tickets", self.customer["api_key"], {"title": "HTTP demo", "body": "Synthetic data"})
            self.assertEqual(code, 201)
            self.assertEqual(request("/tickets/" + result["id"], self.customer["api_key"])[0], 200)
            self.assertEqual(request("/tickets", self.customer["api_key"], {"title": "bad", "body": "bad", "tenant": "two"})[0], 400)
        finally:
            server.shutdown()
            thread.join()
            server.server_close()
