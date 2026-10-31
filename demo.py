import json
import tempfile
from pathlib import Path
from tenantdesk import Desk

with tempfile.TemporaryDirectory() as directory:
    desk = Desk(Path(directory) / "demo.db")
    admin = desk.create_tenant("synthetic-team", "demo-admin")
    customer = desk.add_member(admin["api_key"], "demo-customer", "customer")
    agent = desk.add_member(admin["api_key"], "demo-agent", "agent")
    ticket = desk.create_ticket(customer["api_key"], "Synthetic support request", "Please inspect this demonstration.")
    desk.mutate(admin["api_key"], ticket["id"], expected_revision=0, assignee=agent["id"])
    desk.comment(agent["api_key"], ticket["id"], "Synthetic response recorded.")
    desk.mutate(agent["api_key"], ticket["id"], expected_revision=2, state="resolved")
    desk.mutate(customer["api_key"], ticket["id"], expected_revision=3, state="closed")
    print(json.dumps(desk.get(customer["api_key"], ticket["id"]), indent=2))
