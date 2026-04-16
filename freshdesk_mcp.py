"""
TOKAY Ultimate MCP Server
  • Freshdesk  — direct API
  • Shopify    — routed through Make.com webhook (Make handles OAuth)
Low-level Starlette + uvicorn implementation for Railway deployment.
"""
 
import os
import json
import requests
import uvicorn
 
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.requests import Request
 
# ── Configuration ──────────────────────────────────────────────────────────────
FRESHDESK_SUBDOMAIN = "tokay"
FRESHDESK_API_KEY   = "X4h63JNN8vltLlCFuloj"
BASE_URL            = f"https://{FRESHDESK_SUBDOMAIN}.freshdesk.com/api/v2"
 
# Shopify routed via Make.com (Make holds the Shopify OAuth connection)
MAKE_SHOPIFY_WEBHOOK = os.environ.get(
    "MAKE_SHOPIFY_WEBHOOK",
    "https://hook.eu2.make.com/w6py437q4q0ydhoo1dmf52zygaf7cwhm",
)
 
PORT = int(os.environ.get("PORT", 8000))
 
STATUS_MAP     = {"open": 2, "pending": 3, "resolved": 4, "closed": 5}
PRIORITY_MAP   = {"low": 1, "medium": 2, "high": 3, "urgent": 4}
STATUS_LABEL   = {v: k for k, v in STATUS_MAP.items()}
PRIORITY_LABEL = {v: k for k, v in PRIORITY_MAP.items()}
 
 
def _auth():
    return (FRESHDESK_API_KEY, "X")
 
 
def _headers():
    return {"Content-Type": "application/json"}
 
 
def _call_make(operation: str, **params) -> str:
    """POST to Make.com; Make routes by `operation`, calls Shopify, returns JSON."""
    payload = {"operation": operation, **params}
    try:
        r = requests.post(MAKE_SHOPIFY_WEBHOOK, json=payload, timeout=30)
        r.raise_for_status()
        try:
            return json.dumps(r.json(), indent=2)
        except Exception:
            return r.text or json.dumps({"note": "Empty response from Make"})
    except requests.HTTPError:
        return json.dumps({
            "error": f"Make webhook returned {r.status_code}",
            "body": r.text,
        })
    except Exception as e:
        return json.dumps({"error": f"Failed to call Make: {str(e)}"})
 
 
# ── MCP Server ─────────────────────────────────────────────────────────────────
server = Server("tokay-ops")
 
 
@server.list_tools()
async def list_tools():
    return [
        # ── Freshdesk ──────────────────────────────────────────────────────────
        Tool(name="list_tickets",
             description="List tickets from Freshdesk. Status: open, pending, resolved, closed, all.",
             inputSchema={"type": "object", "properties": {"status": {"type": "string", "default": "open"}, "per_page": {"type": "integer", "default": 20}}}),
        Tool(name="get_ticket",
             description="Get full details of a ticket including conversation thread.",
             inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}}, "required": ["ticket_id"]}),
        Tool(name="reply_to_ticket",
             description="Send a reply to a customer on a ticket.",
             inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "body": {"type": "string"}}, "required": ["ticket_id", "body"]}),
        Tool(name="update_ticket",
             description="Update ticket status, priority, or assignee. Status: open/pending/resolved/closed. Priority: low/medium/high/urgent.",
             inputSchema={"type": "object", "properties": {"ticket_id": {"type": "integer"}, "status": {"type": "string"}, "priority": {"type": "string"}, "assignee_id": {"type": "integer"}}, "required": ["ticket_id"]}),
        Tool(name="create_ticket",
             description="Create a new support ticket.",
             inputSchema={"type": "object", "properties": {"subject": {"type": "string"}, "description": {"type": "string"}, "email": {"type": "string"}, "priority": {"type": "string", "default": "medium"}}, "required": ["subject", "description", "email"]}),
        Tool(name="search_tickets",
             description="Search tickets by keyword.",
             inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}),
        Tool(name="list_contacts",
             description="List customer contacts.",
             inputSchema={"type": "object", "properties": {"per_page": {"type": "integer", "default": 20}}}),
        Tool(name="get_ticket_stats",
             description="Get ticket counts by status (open, pending, resolved, closed).",
             inputSchema={"type": "object", "properties": {}}),
 
        # ── Shopify (via Make.com) ────────────────────────────────────────────
        Tool(name="shopify_search_orders_by_email",
             description="Find Shopify orders for a customer by email. Returns a list of recent orders with items, sizes, status, tracking, shipping address. Use this as the PRIMARY order-lookup tool — most tickets carry a customer email via Freshdesk.",
             inputSchema={"type": "object", "properties": {"email": {"type": "string", "description": "Customer email address"}}, "required": ["email"]}),
        Tool(name="shopify_get_order",
             description="Get full details of a single Shopify order by order number (e.g. '1001'). Use this when the customer explicitly provides an order number; otherwise prefer shopify_search_orders_by_email.",
             inputSchema={"type": "object", "properties": {"order_number": {"type": "string", "description": "Order number, no '#' prefix"}}, "required": ["order_number"]}),
    ]
 
 
@server.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        result = _dispatch(name, arguments)
        return [TextContent(type="text", text=result)]
    except Exception as e:
        return [TextContent(type="text", text=json.dumps({"error": str(e)}))]
 
 
def _dispatch(name: str, args: dict) -> str:
    # ── Shopify via Make ───────────────────────────────────────────────────────
    if name == "shopify_search_orders_by_email":
        return _call_make("search_orders_by_email", email=args["email"])
 
    if name == "shopify_get_order":
        return _call_make("get_order", order_number=str(args["order_number"]))
 
    # ── Freshdesk ──────────────────────────────────────────────────────────────
    if name == "list_tickets":
        params = {"per_page": args.get("per_page", 20), "order_by": "created_at", "order_type": "desc"}
        status = args.get("status", "open")
        if status != "all":
            params["status"] = STATUS_MAP.get(status, 2)
        r = requests.get(f"{BASE_URL}/tickets", headers=_headers(), auth=_auth(), params=params)
        r.raise_for_status()
        tickets = [{"id": t["id"], "subject": t["subject"], "status": STATUS_LABEL.get(t["status"], t["status"]),
                    "priority": PRIORITY_LABEL.get(t["priority"], t["priority"]), "requester": t.get("email", ""),
                    "created_at": t["created_at"], "updated_at": t["updated_at"]} for t in r.json()]
        return json.dumps(tickets, indent=2)
 
    elif name == "get_ticket":
        r = requests.get(f"{BASE_URL}/tickets/{args['ticket_id']}", headers=_headers(), auth=_auth(),
                         params={"include": "conversations,requester"})
        r.raise_for_status()
        data = r.json()
        convs = [{"from": c.get("from_email", "agent"), "body": c.get("body_text", ""), "created": c["created_at"]}
                 for c in data.get("conversations", [])]
        return json.dumps({"id": data["id"], "subject": data["subject"],
                           "description": data.get("description_text", ""),
                           "status": STATUS_LABEL.get(data["status"], data["status"]),
                           "priority": PRIORITY_LABEL.get(data["priority"], data["priority"]),
                           "requester": data.get("requester", {}).get("email", ""),
                           "conversations": convs}, indent=2)
 
    elif name == "reply_to_ticket":
        r = requests.post(f"{BASE_URL}/tickets/{args['ticket_id']}/reply",
                          headers=_headers(), auth=_auth(), json={"body": args["body"]})
        r.raise_for_status()
        return json.dumps({"success": True, "message": f"Reply sent to ticket #{args['ticket_id']}."})
 
    elif name == "update_ticket":
        payload = {}
        if args.get("status"):      payload["status"]       = STATUS_MAP[args["status"]]
        if args.get("priority"):    payload["priority"]     = PRIORITY_MAP[args["priority"]]
        if args.get("assignee_id"): payload["responder_id"] = args["assignee_id"]
        r = requests.put(f"{BASE_URL}/tickets/{args['ticket_id']}",
                         headers=_headers(), auth=_auth(), json=payload)
        r.raise_for_status()
        return json.dumps({"success": True, "message": f"Ticket #{args['ticket_id']} updated."})
 
    elif name == "create_ticket":
        r = requests.post(f"{BASE_URL}/tickets", headers=_headers(), auth=_auth(),
                          json={"subject": args["subject"], "description": args["description"],
                                "email": args["email"], "priority": PRIORITY_MAP.get(args.get("priority", "medium"), 2),
                                "status": 2})
        r.raise_for_status()
        t = r.json()
        return json.dumps({"success": True, "ticket_id": t["id"]})
 
    elif name == "search_tickets":
        r = requests.get(f"{BASE_URL}/search/tickets", headers=_headers(), auth=_auth(),
                         params={"query": f'"{args["query"]}"'})
        r.raise_for_status()
        data = r.json()
        results = [{"id": t["id"], "subject": t["subject"], "status": STATUS_LABEL.get(t["status"], t["status"]),
                    "email": t.get("email", "")} for t in data.get("results", [])]
        return json.dumps({"total": data.get("total", len(results)), "results": results}, indent=2)
 
    elif name == "list_contacts":
        r = requests.get(f"{BASE_URL}/contacts", headers=_headers(), auth=_auth(),
                         params={"per_page": args.get("per_page", 20)})
        r.raise_for_status()
        return json.dumps([{"id": c["id"], "name": c["name"], "email": c["email"]} for c in r.json()], indent=2)
 
    elif name == "get_ticket_stats":
        counts = {}
        for label, code in STATUS_MAP.items():
            r = requests.get(f"{BASE_URL}/tickets", headers=_headers(), auth=_auth(),
                             params={"status": code, "per_page": 1})
            counts[label] = int(r.headers.get("X-Total-Count", len(r.json())))
        return json.dumps(counts, indent=2)
 
    else:
        return json.dumps({"error": f"Unknown tool: {name}"})
 
 
# ── SSE Transport + Starlette app ──────────────────────────────────────────────
sse = SseServerTransport("/messages/")
 
async def handle_sse(request: Request):
    async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
        await server.run(streams[0], streams[1], server.create_initialization_options())
 
app = Starlette(routes=[
    Route("/sse", endpoint=handle_sse),
    Mount("/messages/", app=sse.handle_post_message),
])
 
 
# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"Starting TOKAY MCP server on 0.0.0.0:{PORT}")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
