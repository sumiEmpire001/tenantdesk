# TenantDesk

A local multi-tenant helpdesk API with credential-derived tenant identity, customer/agent/admin roles, tickets, assignment, comments, state transitions and an audit trail. Built for Lord_Sumi's portfolio.

## Run and test

Python 3.10+, standard library only. Run `python -m unittest discover -s tests -v` and `python demo.py`, or use **Actions → Tests and demo → Run workflow**. The demo provisions a temporary synthetic tenant, assigns a ticket, records a response, resolves it and lets its customer close it. No API keys are printed by the demo.

For a local server, run `python -m tenantdesk --db desk.db create-tenant demo admin`. This trusted local provisioning command returns the administrator's random API key once. Set TENANTDESK_ADMIN_KEY in your own terminal to that key, then use `python -m tenantdesk --db desk.db add-member customer customer` or an agent/admin role. Start `python -m tenantdesk --db desk.db serve --port 8080`.

## API routes

Use Authorization: Bearer YOUR_MEMBER_KEY. GET /tickets accepts literal substring q, limit (1..100) and offset. POST /tickets accepts title and body. GET /tickets/ID includes comments. POST /tickets/ID/comments accepts body. POST /tickets/ID/mutate accepts expected_revision and exactly one of assignee (a member ID) or state. No remote request supplies the effective tenant or actor identity; extra creation fields are rejected.

## Permission and state rules

Customers can inspect and comment on their own tickets only. Agents and admins can access tickets in their tenant. Only admins can assign tickets, and targets must be active agents/admins in the same tenant. Customers may close their own resolved ticket; agents/admins resolve work; only admins reopen a closed ticket. Closed tickets reject new comments. Assignment and state changes use an expected revision, preventing simultaneous updates from silently overwriting one another. Comments advance the revision too.

Members are provisioned through the authenticated local administration boundary. Keys are high-entropy random values stored as SHA-256 digests, not plaintext. Disabled keys no longer authorize new operations. The last active admin cannot be disabled. Disabling an assignee does not automatically reassign their tickets; an admin must choose a replacement.

Tickets, comments, revisions and audit records use SQLite transactions. Ticket inspection reads a consistent snapshot. Search is a literal case-insensitive substring, not a wildcard expression. Audit inspection uses the same ticket visibility rules and does not expose API keys.

## Verification and limits

Twelve local tests passed on 31 October 2026, including real HTTP access, cross-tenant isolation, customer ownership, role restrictions, tenant-scoped assignment, comments, lifecycle rules, revision races, key revocation, last-admin protection, audit inspection, search and restart persistence. CI runs the same checks and synthetic demo.

This is a loopback demonstration API, without a web UI, password login, SSO, email delivery, file attachments, TLS termination, abuse limits or multi-host database support. Local provisioning is trusted filesystem administration, not a public sign-up API. Ticket text is stored unencrypted and has no retention/deletion policy. Creation/comment calls do not provide client command-key deduplication; callers must account for ambiguous retries. Read each API response before retrying state changes. Large-tenant performance and external integrations are unbenchmarked.

MIT license; see LICENSE.
