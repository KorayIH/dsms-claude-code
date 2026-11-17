# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "httpx>=0.27"]
# ///
"""Read-only MCP server for a DSMS instance (Dataspace Management System, materials-data.space).

The server reads the access token itself from an env file in the user's home directory
(default ~/.dsms/dsms.env, other path via the environment variable DSMS_ENV). Claude never sees the token:
it only gets the results of the tools below. The token is never returned and is masked in error messages.

There are no tools to create, change or delete anything. SPARQL accepts only SELECT, ASK, CONSTRUCT and
DESCRIBE, and the only POST requests sent are the two read-only endpoints (search and SPARQL query).
"""
from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)

INSTRUCTIONS = """Read-only access to a DSMS instance. Creating, changing and deleting is not possible; point the
user to the DSMS web interface for that.
Workflow: start with list_ktypes and read_ktype to learn the current structure (do not assume it from memory).
Find items with search_kitems or list_kitems, then read_kitem. Use sparql for evaluations across many items.
Raw data is usually a CSV attachment, fetch it with read_attachment.
SPARQL: every item is a node with its URL as IRI, the class is its rdf:type, form fields with a term are triples on
the item itself (form sections do not create nodes), knowledge-item fields are object properties between items,
names are schema:name (https://schema.org/name). Start with a LIMIT.
Rules: never output names, e-mail addresses or phone numbers of people. Give values with unit and the item they
come from. On 401 or 403 pass the message on. Never ask the user for the token."""

mcp = FastMCP("dsms-readonly", instructions=INSTRUCTIONS)


# ---------------------------------------------------------------- connection
def _settings() -> dict[str, str]:
    """DSMS_HOST_URL and DSMS_TOKEN from the env file. Deliberately not from environment variables:
    those would also be visible to Claude's shell."""
    path = Path(os.environ.get("DSMS_ENV") or Path.home() / ".dsms" / "dsms.env").expanduser()
    if not path.exists():
        raise RuntimeError(f"Credentials file missing: {path}. Put DSMS_HOST_URL and DSMS_TOKEN into it.")
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")
    if not values.get("DSMS_HOST_URL") or not values.get("DSMS_TOKEN"):
        raise RuntimeError(f"DSMS_HOST_URL or DSMS_TOKEN missing in {path}.")
    return values


_CLIENT: httpx.Client | None = None
_TOKEN = ""


def _client() -> httpx.Client:
    global _CLIENT, _TOKEN
    if _CLIENT is None:
        s = _settings()
        _TOKEN = s["DSMS_TOKEN"].removeprefix("Bearer ").strip()
        _CLIENT = httpx.Client(base_url=s["DSMS_HOST_URL"].rstrip("/") + "/",
                               headers={"Authorization": f"Bearer {_TOKEN}"}, timeout=60)
    return _CLIENT


def _check(r: httpx.Response) -> httpx.Response:
    if r.status_code == 401:
        raise RuntimeError("401: token expired or invalid. Put a new token into the credentials file.")
    if r.status_code == 403:
        raise RuntimeError("403: access denied (IP allow list or missing permissions).")
    if not r.is_success:
        text = r.text[:500].replace(_TOKEN, "***") if _TOKEN else r.text[:500]
        raise RuntimeError(f"HTTP {r.status_code}: {text}")
    return r


def _get(path: str, **params) -> httpx.Response:
    return _check(_client().get(path, params={k: v for k, v in params.items() if v is not None}))


_READ_ONLY_POST = ("api/knowledge/kitems/search", "api/knowledge/sparql")


def _post_read_only(path: str, **kw) -> httpx.Response:
    """Only for the two read-only POST endpoints (search, SPARQL query)."""
    if path not in _READ_ONLY_POST:
        raise RuntimeError("Not allowed.")
    return _check(_client().post(path, **kw))


# ---------------------------------------------------------------- helpers
def _value(v):
    if isinstance(v, list):
        return [x.get("name", x.get("id")) if isinstance(x, dict) else x for x in v]
    return v


def _fields(custom_properties: dict | None) -> list[dict]:
    out = []
    for sec in ((custom_properties or {}).get("content") or {}).get("sections", []) or []:
        for e in sec.get("entries", []) or []:
            rm = e.get("relationMapping") or {}
            unit = e.get("measurementUnit") or {}
            out.append({
                "section": sec.get("name"),
                "field": e.get("label"),
                "value": _value(e.get("value")),
                "unit": (unit.get("symbol") or unit.get("label")) if isinstance(unit, dict) else unit,
                "term": rm.get("iri"),
            })
    return out


# ---------------------------------------------------------------- tools
@mcp.tool()
def list_ktypes() -> list[dict]:
    """All K-Types (kinds of items) of the instance with ID and name."""
    data = _get("api/knowledge-type/").json()
    items = data if isinstance(data, list) else data.get("ktypes", data)
    return [{"id": k.get("id"), "name": k.get("name")} for k in items]


@mcp.tool()
def read_ktype(ktype_id: str) -> dict:
    """Structure of a K-Type: fields with widget, unit and term (IRI), plus the class mapping."""
    k = _get(f"api/knowledge-type/{quote(ktype_id)}").json()
    form = k.get("custom_properties") or k.get("webform") or {}
    fields = []
    for sec in form.get("sections", []) or []:
        for inp in sec.get("inputs", []) or []:
            rm = inp.get("relationMapping") or {}
            mu = inp.get("measurementUnit")
            fields.append({"section": sec.get("name"), "field": inp.get("label"), "widget": inp.get("widget"),
                           "unit": (mu.get("symbol") or mu.get("label")) if isinstance(mu, dict) else mu,
                           "term": rm.get("iri"), "kind": rm.get("type"),
                           "refers_to": inp.get("knowledgeType") or None,
                           "options": [o.get("label", o) if isinstance(o, dict) else o
                                       for o in inp.get("selectOptions") or []] or None})
    return {"id": k.get("id"), "name": k.get("name"), "description": form.get("description"),
            "classes": form.get("classMapping"), "fields": fields}


@mcp.tool()
def list_kitems(ktype_id: str | None = None, limit: int = 200, offset: int = 0) -> dict:
    """List items (K-Items), optionally of one K-Type only. Returns ID, name and K-Type."""
    d = _get("api/knowledge/kitems", limit=1000 if ktype_id else limit, offset=0 if ktype_id else offset).json()
    items = d.get("kitems", [])
    if ktype_id:
        items = [k for k in items if k.get("ktype_id") == ktype_id][offset:offset + limit]
    return {"total_in_instance": d.get("total_count"),
            "items": [{"id": k["id"], "name": k["name"], "ktype": k["ktype_id"]} for k in items]}


@mcp.tool()
def search_kitems(term: str, ktype_ids: list[str] | None = None, limit: int = 25) -> list[dict]:
    """Full-text search across the items, optionally restricted to K-Types."""
    payload = {"search_term": term, "ktypes": ktype_ids or [], "annotations": [],
               "limit": limit, "offset": 0, "compact": True}
    d = _post_read_only("api/knowledge/kitems/search", json=payload, params={"allow_fuzzy": True}).json()
    hits = d.get("hits", d) if isinstance(d, dict) else d
    out = []
    for h in hits:
        k = h.get("kitem", h)
        out.append({"id": k.get("id"), "name": k.get("name"), "ktype": k.get("ktype_id")})
    return out


@mcp.tool()
def read_kitem(kitem_id: str) -> dict:
    """One item with all form fields, links and attachments."""
    k = _get(f"api/knowledge/kitems/{quote(kitem_id)}").json()
    return {
        "id": k.get("id"), "name": k.get("name"), "ktype": k.get("ktype_id"),
        "created": k.get("created_at"), "updated": k.get("updated_at"),
        "fields": _fields(k.get("custom_properties")),
        "links": [{"relation": l.get("label") or l.get("iri"), "incoming": l.get("is_incoming"),
                   "target": (l.get("kitem") or {}).get("name"), "target_id": (l.get("kitem") or {}).get("id"),
                   "target_ktype": (l.get("kitem") or {}).get("ktype_id")} for l in k.get("linked_kitems") or []],
        "attachments": [a.get("name") for a in k.get("attachments") or []],
    }


@mcp.tool()
def read_attachment(kitem_id: str, filename: str, max_lines: int = 300) -> dict:
    """Load an attachment of an item. Text and CSV come back as text (cut to max_lines), other files
    (images, PDF) are saved to a temporary folder and the path is returned."""
    r = _get(f"api/knowledge/attachments/{quote(kitem_id)}/{quote(filename)}")
    if Path(filename).suffix.lower() in (".csv", ".txt", ".md", ".json", ".ttl", ".tsv"):
        lines = r.content.decode("utf-8", errors="replace").splitlines()
        return {"file": filename, "lines_total": len(lines), "content": "\n".join(lines[:max_lines])}
    target = Path(tempfile.gettempdir()) / "dsms-readonly" / Path(kitem_id).name
    target.mkdir(parents=True, exist_ok=True)
    (target / Path(filename).name).write_bytes(r.content)
    return {"file": filename, "saved_to": str(target / Path(filename).name), "bytes": len(r.content)}


_READ_FORMS = {"SELECT", "ASK", "CONSTRUCT", "DESCRIBE"}
_UPDATE_WORDS = re.compile(r"\b(INSERT|DELETE|LOAD|CLEAR|DROP|CREATE|ADD|MOVE|COPY|WITH)\b", re.I)


def _read_only(query: str) -> None:
    stripped = re.sub(r'"[^"\n]*"|\'[^\'\n]*\'|<[^>\s]*>|#[^\n]*', " ", query)   # literals, IRIs, comments
    body = re.sub(r"(?is)^\s*((PREFIX\s+\S*\s*|BASE\s+)\s*)*", "", stripped)
    first = (body.split() or [""])[0].upper()
    if first not in _READ_FORMS or _UPDATE_WORDS.search(stripped):
        raise RuntimeError("Only read-only SPARQL queries (SELECT, ASK, CONSTRUCT, DESCRIBE) are allowed.")


@mcp.tool()
def sparql(query: str) -> dict:
    """Read-only SPARQL query over the knowledge graph of the items (SELECT, ASK, CONSTRUCT, DESCRIBE).
    For SELECT: column names and rows (at most 1000)."""
    _read_only(query)
    d = _post_read_only("api/knowledge/sparql", data={"query": query},
                        params={"repository": "knowledge-items"}).json()
    if isinstance(d, dict) and "results" in d:
        cols = d.get("head", {}).get("vars", [])
        rows = [{c: b.get(c, {}).get("value") for c in cols} for b in d["results"].get("bindings", [])]
        return {"columns": cols, "rows": rows[:1000], "count": len(rows)}
    return {"result": d}


@mcp.tool()
def search_terms(word: str, kind: str = "class", limit: int = 50) -> list[dict]:
    """Search terms of the loaded ontologies. kind: class, object_property, data_property or measurement_unit."""
    d = _get("api/vocabulary/search", query=word, type=kind, limit=limit).json()
    items = d if isinstance(d, list) else d.get("results", d.get("items", []))
    return [{"iri": t.get("iri"), "label": t.get("label"), "kind": t.get("type"), "namespace": t.get("namespace")}
            for t in items if isinstance(t, dict)]


if __name__ == "__main__":
    mcp.run()
