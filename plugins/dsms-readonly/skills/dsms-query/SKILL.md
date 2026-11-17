---
name: dsms-query
description: Query data in a DSMS instance (Dataspace Management System, materials-data.space), read-only through the MCP server dsms. Use for questions about items (K-Items) and their kinds (K-Types), measurements, properties, attachments with raw data, SPARQL queries over the DSMS knowledge graph and searching terms of the loaded ontologies.
---

# Query the DSMS (read-only)

The MCP server `dsms` has read tools only. Creating, changing and deleting is not possible. If someone wants a change,
point them to the DSMS web interface.

## Tools

| Tool | Purpose |
|---|---|
| `list_ktypes` | which kinds of items exist |
| `read_ktype` | fields of a K-Type with unit, term (IRI) and link target |
| `list_kitems` | items of a K-Type (ID, name) |
| `search_kitems` | full-text search over names and content |
| `read_kitem` | all fields, links and attachments of an item |
| `read_attachment` | CSV and text directly, images and PDF as a local file |
| `sparql` | read-only queries over the graph (SELECT, ASK, CONSTRUCT, DESCRIBE) |
| `search_terms` | terms of the loaded ontologies (class, object_property, data_property, measurement_unit) |

## Workflow

1. For a new question, check the structure first with `list_ktypes` and `read_ktype`. The content changes; do not
   assume K-Types and fields from memory.
2. Find single items with `search_kitems` or `list_kitems`, then `read_kitem`.
3. For evaluations across many items use SPARQL instead of reading items one by one.
4. Raw data is usually a CSV attachment. Fetch it with `read_attachment`, limit large files with `max_lines`.

## SPARQL notes

- Every item is a node with its URL as IRI, e.g. `https://<instance>/knowledge/<ktype>/<slug>`.
- The class is the `rdf:type`, as defined in the `classMapping` of the K-Type.
- Form fields with a term become triples on the item itself. Form sections do not create nodes.
- Knowledge-item fields become object properties between the items.
- Names are `schema:name` (`https://schema.org/name`). Not every item has an `rdfs:label`.
- Start with `LIMIT` and widen the query once it is right.

Example: number of items per class

```sparql
SELECT ?class (COUNT(?s) AS ?n) WHERE { ?s a ?class } GROUP BY ?class ORDER BY DESC(?n) LIMIT 20
```

## Rules

- Never output names, e-mail addresses or phone numbers of people, also not from items such as experts or contacts.
- Do not send DSMS data to external services.
- On 401 (token expired) or 403 (IP not allowed) pass the message on. Never ask for the token and never try to
  read the credentials file.
- Always give values with their unit and the item they come from.
