import hashlib
import json
import secrets
import sqlite3
import time
import uuid
from contextlib import contextmanager


class Conflict(ValueError):
    pass


def content(value, maximum):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(f"Text must contain 1..{maximum} characters")


class Desk:
    def __init__(self, path="desk.db", clock=time.time):
        if str(path) == ":memory:":
            raise ValueError("Use a filesystem database")
        self.path, self.clock = str(path), clock
        with self.db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS tenants(id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS members(id INTEGER PRIMARY KEY,tenant TEXT NOT NULL REFERENCES tenants(id),name TEXT NOT NULL,
                    role TEXT NOT NULL,key_hash TEXT UNIQUE NOT NULL,enabled INTEGER NOT NULL,UNIQUE(tenant,name));
                CREATE TABLE IF NOT EXISTS tickets(id TEXT PRIMARY KEY,tenant TEXT NOT NULL REFERENCES tenants(id),owner INTEGER NOT NULL REFERENCES members(id),
                    title TEXT NOT NULL,body TEXT NOT NULL,state TEXT NOT NULL,assignee INTEGER REFERENCES members(id),revision INTEGER NOT NULL,created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS comments(id INTEGER PRIMARY KEY,ticket TEXT NOT NULL REFERENCES tickets(id),actor INTEGER NOT NULL REFERENCES members(id),body TEXT NOT NULL,at REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS audit(seq INTEGER PRIMARY KEY,tenant TEXT NOT NULL,ticket TEXT,actor INTEGER NOT NULL,event TEXT NOT NULL,detail TEXT NOT NULL,at REAL NOT NULL);
            """)

    @contextmanager
    def db(self, write=False):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db
            if write:
                db.commit()
        except BaseException:
            if write:
                db.rollback()
            raise
        finally:
            db.close()

    def actor(self, db, key):
        if not isinstance(key, str) or not 1 <= len(key) <= 256:
            raise PermissionError("Unauthorized")
        row = db.execute("SELECT id,tenant,name,role FROM members WHERE key_hash=? AND enabled=1", (hashlib.sha256(key.encode()).hexdigest(),)).fetchone()
        if not row:
            raise PermissionError("Unauthorized")
        return row

    def log(self, db, actor, ticket, event, detail):
        db.execute("INSERT INTO audit(tenant,ticket,actor,event,detail,at) VALUES(?,?,?,?,?,?)", (actor["tenant"], ticket, actor["id"], event, json.dumps(detail), self.clock()))

    def _member(self, db, tenant, name, role):
        content(name, 128)
        if role not in ("admin", "agent", "customer"):
            raise ValueError("Unknown member role")
        if db.execute("SELECT id FROM members WHERE tenant=? AND name=?", (tenant, name)).fetchone():
            raise ValueError("Member name already exists in tenant")
        key = secrets.token_urlsafe(32)
        row = db.execute("INSERT INTO members(tenant,name,role,key_hash,enabled) VALUES(?,?,?,?,1)", (tenant, name, role, hashlib.sha256(key.encode()).hexdigest()))
        return {"id": row.lastrowid, "name": name, "role": role, "api_key": key}

    def create_tenant(self, tenant, admin):
        """Trusted local provisioning operation; not exposed through the HTTP API."""
        content(tenant, 128)
        with self.db(True) as db:
            if db.execute("SELECT id FROM tenants WHERE id=?", (tenant,)).fetchone():
                raise ValueError("Tenant already exists")
            db.execute("INSERT INTO tenants VALUES(?)", (tenant,))
            return self._member(db, tenant, admin, "admin")

    def add_member(self, key, name, role):
        with self.db(True) as db:
            actor = self.actor(db, key)
            if actor["role"] != "admin":
                raise PermissionError("Administrator role required")
            result = self._member(db, actor["tenant"], name, role)
            self.log(db, actor, None, "member_created", {"member": result["id"], "role": role})
            return result

    def disable_member(self, key, member):
        if type(member) is not int or member < 1:
            raise ValueError("Member ID must be a positive integer")
        with self.db(True) as db:
            actor = self.actor(db, key)
            if actor["role"] != "admin":
                raise PermissionError("Administrator role required")
            target = db.execute("SELECT id,role FROM members WHERE id=? AND tenant=? AND enabled=1", (member, actor["tenant"])).fetchone()
            if not target:
                raise ValueError("Active member not found")
            if target["role"] == "admin" and db.execute("SELECT COUNT(*) FROM members WHERE tenant=? AND role='admin' AND enabled=1", (actor["tenant"],)).fetchone()[0] <= 1:
                raise ValueError("Cannot disable the last administrator")
            db.execute("UPDATE members SET enabled=0 WHERE id=?", (member,))
            self.log(db, actor, None, "member_disabled", {"member": member})

    def accessible(self, db, actor, ticket):
        row = db.execute("SELECT * FROM tickets WHERE id=? AND tenant=?", (ticket, actor["tenant"])).fetchone()
        if not row or (actor["role"] == "customer" and row["owner"] != actor["id"]):
            raise PermissionError("Ticket unavailable")
        return row

    def create_ticket(self, key, title, body):
        content(title, 140)
        content(body, 4000)
        with self.db(True) as db:
            actor = self.actor(db, key)
            ticket = uuid.uuid4().hex
            db.execute("INSERT INTO tickets VALUES(?,?,?,?,?,'open',NULL,0,?)", (ticket, actor["tenant"], actor["id"], title, body, self.clock()))
            self.log(db, actor, ticket, "created", {})
            return dict(self.accessible(db, actor, ticket))

    def get(self, key, ticket):
        with self.db() as db:
            db.execute("BEGIN")
            actor = self.actor(db, key)
            row = dict(self.accessible(db, actor, ticket))
            row["comments"] = [dict(c) for c in db.execute("SELECT * FROM comments WHERE ticket=? ORDER BY id", (ticket,))]
            return row

    def tickets(self, key, query="", *, limit=50, offset=0):
        if type(limit) is not int or not 1 <= limit <= 100 or type(offset) is not int or offset < 0 or not isinstance(query, str) or len(query) > 256:
            raise ValueError("Invalid search or pagination")
        with self.db() as db:
            actor = self.actor(db, key)
            # Literal substring search: wildcard characters do not act as SQL patterns.
            rows = db.execute("SELECT * FROM tickets WHERE tenant=? AND (?!='customer' OR owner=?) AND instr(lower(title),lower(?))>0 ORDER BY created,id LIMIT ? OFFSET ?", (actor["tenant"], actor["role"], actor["id"], query, limit, offset))
            return [dict(row) for row in rows]

    def comment(self, key, ticket, body):
        content(body, 4000)
        with self.db(True) as db:
            actor = self.actor(db, key)
            row = self.accessible(db, actor, ticket)
            if row["state"] == "closed":
                raise ValueError("Closed tickets cannot receive comments")
            db.execute("INSERT INTO comments(ticket,actor,body,at) VALUES(?,?,?,?)", (ticket, actor["id"], body, self.clock()))
            db.execute("UPDATE tickets SET revision=revision+1 WHERE id=?", (ticket,))
            self.log(db, actor, ticket, "commented", {})

    def mutate(self, key, ticket, *, expected_revision, assignee=None, state=None):
        if type(expected_revision) is not int or expected_revision < 0 or (assignee is None) == (state is None):
            raise ValueError("Supply expected revision and exactly one mutation")
        if assignee is not None and (type(assignee) is not int or assignee < 1):
            raise ValueError("Assignee ID must be a positive integer")
        if state is not None and not isinstance(state, str):
            raise ValueError("State must be a string")
        with self.db(True) as db:
            actor = self.actor(db, key)
            row = self.accessible(db, actor, ticket)
            if row["revision"] != expected_revision:
                raise Conflict("Ticket changed; refresh before applying mutation")
            if assignee is not None:
                if actor["role"] != "admin":
                    raise PermissionError("Only administrators assign tickets")
                target = db.execute("SELECT id FROM members WHERE id=? AND tenant=? AND role IN ('admin','agent') AND enabled=1", (assignee, actor["tenant"])).fetchone()
                if not target:
                    raise ValueError("Assignee must be an active agent or admin in this tenant")
                db.execute("UPDATE tickets SET assignee=?,revision=revision+1 WHERE id=?", (assignee, ticket))
                self.log(db, actor, ticket, "assigned", {"member": assignee})
            else:
                transitions = {"open": {"in_progress", "resolved"}, "in_progress": {"open", "resolved"}, "resolved": {"open", "closed"}, "closed": {"open"}}
                if state not in transitions[row["state"]]:
                    raise ValueError("Invalid status transition")
                if actor["role"] == "customer" and not (row["state"] == "resolved" and state == "closed"):
                    raise PermissionError("Customers may only close their own resolved ticket")
                if row["state"] == "closed" and actor["role"] != "admin":
                    raise PermissionError("Only administrators reopen closed tickets")
                db.execute("UPDATE tickets SET state=?,revision=revision+1 WHERE id=?", (state, ticket))
                self.log(db, actor, ticket, "state_changed", {"from": row["state"], "to": state})
            return dict(self.accessible(db, actor, ticket))

    def audit(self, key, ticket):
        with self.db() as db:
            actor = self.actor(db, key)
            self.accessible(db, actor, ticket)
            return [dict(row) for row in db.execute("SELECT seq,actor,event,detail,at FROM audit WHERE ticket=? AND tenant=? ORDER BY seq", (ticket, actor["tenant"]))]
