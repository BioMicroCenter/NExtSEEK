from __future__ import annotations

import json

SCHEMA = [
    ("CREATE TABLE IF NOT EXISTS sample_types (id BIGINT PRIMARY KEY,title VARCHAR(255) NOT NULL,updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))", ()),
    ("CREATE TABLE IF NOT EXISTS sample_attribute_types (id BIGINT PRIMARY KEY,title VARCHAR(255) NOT NULL)", ()),
    ("CREATE TABLE IF NOT EXISTS units (id BIGINT PRIMARY KEY,title VARCHAR(255) NOT NULL,symbol VARCHAR(255) NULL)", ()),
    ("CREATE TABLE IF NOT EXISTS sample_controlled_vocabs (id BIGINT PRIMARY KEY,title VARCHAR(255) NOT NULL)", ()),
    ("CREATE TABLE IF NOT EXISTS sample_attributes (id BIGINT PRIMARY KEY,sample_type_id BIGINT NOT NULL,sample_attribute_type_id BIGINT NOT NULL,title VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,required TINYINT(1) NOT NULL DEFAULT 0,pos INT NOT NULL,is_title TINYINT(1) NOT NULL DEFAULT 0,description TEXT NULL,unit_id BIGINT NULL,sample_controlled_vocab_id BIGINT NULL,linked_sample_type_id BIGINT NULL,created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))", ()),
    ("CREATE TABLE IF NOT EXISTS samples (id BIGINT PRIMARY KEY,sample_type_id BIGINT NOT NULL,json_metadata LONGTEXT NOT NULL,updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6))", ()),
]
CHECKSUM_COLUMNS = {
    "sample_types": ("id", "title", "updated_at"),
    "sample_attribute_types": ("id", "title"),
    "units": ("id", "title", "symbol"),
    "sample_controlled_vocabs": ("id", "title"),
    "sample_attributes": ("id", "sample_type_id", "sample_attribute_type_id", "title", "required", "pos", "is_title", "description", "unit_id", "sample_controlled_vocab_id", "linked_sample_type_id", "created_at", "updated_at"),
    "samples": ("id", "sample_type_id", "json_metadata", "updated_at"),
}


def _structured(value):
    if set(value) != {"sample_type_id", "sample_titles", "samples"}:
        raise ValueError("structured SEEK fixture keys are exact")
    type_id = value["sample_type_id"]
    titles, samples = value["sample_titles"], value["samples"]
    if not isinstance(type_id, int) or isinstance(type_id, bool) or type_id <= 0:
        raise ValueError("sample_type_id must be positive")
    if not isinstance(titles, list) or not titles or any(not isinstance(item, str) or not item.strip() for item in titles):
        raise ValueError("sample_titles must be nonempty strings")
    statements = list(SCHEMA)
    statements += [
        ("INSERT INTO sample_attribute_types(id,title) VALUES(%s,%s) ON DUPLICATE KEY UPDATE title=VALUES(title)", (1, "String")),
        ("INSERT INTO sample_types(id,title) VALUES(%s,%s) ON DUPLICATE KEY UPDATE title=VALUES(title)", (type_id, f"Type {type_id}")),
    ]
    for position, title in enumerate(titles, 1):
        statements.append(("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title) VALUES(%s,%s,1,%s,%s,%s,%s) ON DUPLICATE KEY UPDATE title=VALUES(title),pos=VALUES(pos)",
                           (type_id * 100000 + position, type_id, title, position == 1, position, position == 1)))
    for row in samples:
        if set(row) != {"id", "json_metadata"} or not isinstance(row["id"], int) or not isinstance(row["json_metadata"], dict):
            raise ValueError("sample rows require exact id/json_metadata")
        statements.append(("INSERT INTO samples(id,sample_type_id,json_metadata) VALUES(%s,%s,%s) ON DUPLICATE KEY UPDATE sample_type_id=VALUES(sample_type_id),json_metadata=VALUES(json_metadata)",
                           (row["id"], type_id, json.dumps(row["json_metadata"], separators=(",", ":"), sort_keys=True))))
    return statements


def _named(name):
    if name == "attribute_schema_empty":
        return list(SCHEMA)
    if name in {"attribute_schema_unique", "attribute_planner_zero_write", "attribute_repository_one"}:
        return _structured({"sample_type_id": 7, "sample_titles": ["UID", "Mass"], "samples": [{"id": 1, "json_metadata": {"UID": "u1", "Mass": "1"}}]})
    if name == "attribute_schema_case_duplicate":
        statements = _structured({"sample_type_id": 7, "sample_titles": ["UID", "Mass"], "samples": [{"id": 1, "json_metadata": {"UID": "u1"}}]})
        statements.append(("INSERT INTO sample_attributes(id,sample_type_id,sample_attribute_type_id,title,required,pos,is_title) VALUES(%s,%s,1,%s,0,%s,0)", (700003, 7, "mass", 3)))
        return statements
    if name in {"attribute_repository_5000", "attribute_planner_scale_5000"}:
        return _structured({"sample_type_id": 8, "sample_titles": ["UID", *[f"A{n}" for n in range(1, 5000)]], "samples": [{"id": 2, "json_metadata": {"UID": "u2"}}]})
    raise ValueError(f"unknown named SEEK fixture: {name}")


def compile_seek_fixture(fixture):
    return _named(fixture) if isinstance(fixture, str) else _structured(fixture) if isinstance(fixture, dict) else (_ for _ in ()).throw(ValueError("fixture must be a frozen name or object"))


def compile_checksum_query(table, where):
    if table not in CHECKSUM_COLUMNS or any(key not in CHECKSUM_COLUMNS[table] for key in where):
        raise ValueError("checksum table/column is not allowlisted")
    clauses = [f"`{key}`=%s" for key in sorted(where)]
    sql = f"SELECT {','.join(f'`{column}`' for column in CHECKSUM_COLUMNS[table])} FROM `{table}`"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY `id`"
    return sql, tuple(where[key] for key in sorted(where))
