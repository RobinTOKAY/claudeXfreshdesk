#!/usr/bin/env python3
"""
Freshdesk MCP Server — TOKAY Ultimate
Exposes Freshdesk customer support tools to Claude via the Model Context Protocol.
"""

import json
import requests
from mcp.server.fastmcp import FastMCP

# ── Configuration ──────────────────────────────────────────────────────────────
FRESHDESK_SUBDOMAIN = "tokay"
FRESHDESK_API_KEY   = "X4h63JNN8vltLlCFuloj"
BASE_URL            = f"https://{FRESHDESK_SUBDOMAIN}.freshdesk.com/api/v2"

# ── Helpers ────────────────────────────────────────────────────────────────────
def _headers() -> dict:
    return {
        "Content-Type": "application/json",
    }

def _auth():
    return (FRESHDESK_API_KEY, "X")

STATUS_MAP   = {"open": 2, "pending": 3, "resolved": 4, "closed": 5}
PRIORITY_MAP = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
STATUS_LABEL   = {v: k for k, v in STATUS_MAP.items()}
PRIORITY_LABEL = {v: k for k, v in PRIORITY_MAP.items()}

# ── MCP Server ─────────────────────────────────────────────────────────────────
mcp = FastMCP("Freshdesk — TOKAY Ultimate")


@mcp.tool()
def list_tickets(status: str = "open", per_page: int = 20) -> str:
    """
    List tickets from Freshdesk.

    Args:
        status:   Filter by status. One of: open, pending, resolved, closed, all.
        per_page: Number of tickets to return (max 100).
    """
    params: dict = {"per_page": per_page, "order_by": "created_at", "order_type": "desc"}
    if status != "all":
        params["status"] = STATUS_MAP.get(status, 2)

    r = requests.get(f"{BASE_URL}/tickets", headers=_headers(), auth=_auth(), params=params)
    r.raise_for_status()

    tickets = []
    for t in r.json():
        tickets.append({
            "id":         t["id"],
            "subject":    t["subject"],
            "status":     STATUS_LABEL.get(t["status"], t["status"]),
            "priority":   PRIORITY_LABEL.get(t["priority"], t["priority"]),
            "requester":  t.get("email", ""),
            "created_at": t["created_at"],
            "updated_at": t["updated_at"],
        })
    return json.dumps(tickets, indent=2)


@mcp.tool()
def get_ticket(ticket_id: int) -> str:
    """
    Get full details of a ticket, including its conversation thread.

    Args:
        ticket_id: The numeric Freshdesk ticket ID.
    """
    r = requests.get(
        f"{BASE_URL}/tickets/{ticket_id}",
        headers=_headers(), auth=_auth(),
        params={"include": "conversations,requester"}
    )
    r.raise_for_status()
    data = r.json()

    # Simplify conversations for readability
    conversations = []
    for c in data.get("conversations", []):
        conversations.append({
            "from":    c.get("from_email", "agent"),
            "body":    c.get("body_text", c.get("body", "")),
            "created": c["created_at"],
        })

    result = {
        "id":            data["id"],
        "subject":       data["subject"],
        "description":   data.get("description_text", data.get("description", "")),
        "status":        STATUS_LABEL.get(data["status"], data["status"]),
        "priority":      PRIORITY_LABEL.get(data["priority"], data["priority"]),
        "requester":     data.get("requester", {}).get("email", ""),
        "created_at":    data["created_at"],
        "updated_at":    data["updated_at"],
        "conversations": conversations,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def reply_to_ticket(ticket_id: int, body: str) -> str:
    """
    Send a reply to a customer on a ticket.

    Args:
        ticket_id: The numeric Freshdesk ticket ID.
        body:      The reply text (plain text or HTML).
    """
    r = requests.post(
        f"{BASE_URL}/tickets/{ticket_id}/reply",
        headers=_headers(), auth=_auth(),
        json={"body": body}
    )
    r.raise_for_status()
    return json.dumps({"success": True, "message": f"Reply sent to ticket #{ticket_id}."})


@mcp.tool()
def update_ticket(
    ticket_id:   int,
    status:      str = None,
    priority:    str = None,
    assignee_id: int = None,
    tags:        list[str] = None,
) -> str:
    """
    Update properties of an existing ticket.

    Args:
        ticket_id:   The numeric Freshdesk ticket ID.
        status:      New status: open, pending, resolved, closed.
        priority:    New priority: low, medium, high, urgent.
        assignee_id: Freshdesk agent ID to assign the ticket to.
        tags:        List of tags to set on the ticket.
    """
    payload: dict = {}
    if status:      payload["status"]       = STATUS_MAP[status]
    if priority:    payload["priority"]     = PRIORITY_MAP[priority]
    if assignee_id: payload["responder_id"] = assignee_id
    if tags:        payload["tags"]         = tags

    r = requests.put(
        f"{BASE_URL}/tickets/{ticket_id}",
        headers=_headers(), auth=_auth(),
        json=payload
    )
    r.raise_for_status()
    return json.dumps({"success": True, "message": f"Ticket #{ticket_id} updated."})


@mcp.tool()
def create_ticket(
    subject:     str,
    description: str,
    email:       str,
    priority:    str = "medium",
) -> str:
    """
    Create a new support ticket.

    Args:
        subject:     Ticket subject line.
        description: Full ticket description / body.
        email:       Requester's email address.
        priority:    low, medium, high, or urgent.
    """
    r = requests.post(
        f"{BASE_URL}/tickets",
        headers=_headers(), auth=_auth(),
        json={
            "subject":     subject,
            "description": description,
            "email":       email,
            "priority":    PRIORITY_MAP.get(priority, 2),
            "status":      2,  # open
        }
    )
    r.raise_for_status()
    ticket = r.json()
    return json.dumps({"success": True, "ticket_id": ticket["id"], "message": f"Ticket #{ticket['id']} created."})


@mcp.tool()
def search_tickets(query: str) -> str:
    """
    Search tickets by keyword (searches subject, description, and conversations).

    Args:
        query: Search string.
    """
    r = requests.get(
        f"{BASE_URL}/search/tickets",
        headers=_headers(), auth=_auth(),
        params={"query": f'"{query}"'}
    )
    r.raise_for_status()
    data = r.json()
    results = data.get("results", [])

    simplified = []
    for t in results:
        simplified.append({
            "id":       t["id"],
            "subject":  t["subject"],
            "status":   STATUS_LABEL.get(t["status"], t["status"]),
            "priority": PRIORITY_LABEL.get(t["priority"], t["priority"]),
            "email":    t.get("email", ""),
        })
    return json.dumps({"total": data.get("total", len(simplified)), "results": simplified}, indent=2)


@mcp.tool()
def list_contacts(per_page: int = 20) -> str:
    """
    List customer contacts registered in Freshdesk.

    Args:
        per_page: Number of contacts to return (max 100).
    """
    r = requests.get(
        f"{BASE_URL}/contacts",
        headers=_headers(), auth=_auth(),
        params={"per_page": per_page}
    )
    r.raise_for_status()
    contacts = [
        {"id": c["id"], "name": c["name"], "email": c["email"], "phone": c.get("phone", "")}
        for c in r.json()
    ]
    return json.dumps(contacts, indent=2)


@mcp.tool()
def get_ticket_stats() -> str:
    """
    Return a summary of ticket counts by status — useful for a quick dashboard overview.
    """
    counts = {}
    for label, code in STATUS_MAP.items():
        r = requests.get(
            f"{BASE_URL}/tickets",
            headers=_headers(), auth=_auth(),
            params={"status": code, "per_page": 1}
        )
        # Freshdesk returns X-Total-Count header
        counts[label] = int(r.headers.get("X-Total-Count", len(r.json())))

    return json.dumps(counts, indent=2)


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    mcp.run()