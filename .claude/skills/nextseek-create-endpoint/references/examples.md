# Real identifiers for OpenAPI examples

Use these in `OpenApiExample` values and `*_DESC` **EXAMPLES** bullets. Do not invent placeholder UIDs when a real one exists.

## Samples

| UID | Role |
|---|---|
| `NHP-220630FLY-1-PUB` | Non-human primate, public |
| `NHP-220630FLY-2-PUB` | Derived NHP |
| `TIS-230324BOO-39-PUB` | Tissue sample |
| `TIS-230324BOO-40-PUB` | Tissue sample (pair) |
| `PAT-241113DFC-3` | Patient/subject |

Numeric SEEK sample id example: `321`, `1525`.

## Sample types

| Type | SEEK id | Clade |
|---|---|---|
| NHP | 41 | Subject |
| TIS | 26 | Sample |
| D.IMG | 40 | Data |

## Projects, studies, assays

| Entity | ID | Title / note |
|---|---|---|
| Project | `2558` | Water toxicity / primate study context |
| Project | `1` | Default dev project for user minting |
| Study | `746` | Vaccine Dose Response |
| Investigation | `763` | Parent of study 746 |
| Assay | `351` | Example flow cytometry / RNA-seq assay |
| Person | `1652` | Researcher profile |
| SOP | `142` | Protocol document |

## Full OpenApiExample — study fetch (response)

```python
OpenApiExample(
    name="Get Study",
    value={
        "data": {
            "id": "746",
            "type": "studies",
            "attributes": {"title": "Vaccine Dose Response"},
            "relationships": {
                "investigation": {"data": {"id": "763", "type": "investigations"}}
            },
            "links": {"self": "/studies/746"},
            "meta": {},
        },
        "jsonapi": {"version": "1.0"},
    },
    response_only=True,
)
```

## Full OpenApiExample — admin sample retrieve (request)

```python
OpenApiExample(
    name="JSON output (default)",
    value={"identifiers": ["NHP-220630FLY-1-PUB", "TIS-230324BOO-39-PUB"]},
    request_only=True,
)
```

## Full OpenApiExample — study create (request)

```python
OpenApiExample(
    name="Create Study",
    value={
        "data": {
            "type": "studies",
            "attributes": {
                "title": "Vaccine Dose Response",
                "description": "Comparison of immune response across doses",
                "experimentalists": "Wet lab team",
            },
            "relationships": {
                "investigation": {"data": {"id": "763", "type": "investigations"}}
            },
        }
    },
    request_only=True,
)
```
