# R26 live-gate prerequisites runbook (Gate 3C design-ready)

Status: **R26-design-ready** (checks defined + committed). **R26-closed** only after
execution on dev in Gate 3D with evidence attached.

## 1. Existing project binding (no greenfield create)

```bash
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT id, title FROM seek_production.projects WHERE id=1;"
```

**Pass:** row `1	Published Data` matches `instance_binding.json` (`project_id`, `project_title`).

## 2. Sample count probe

```bash
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT COUNT(*) FROM seek_production.samples;"
```

**Pass:** count ≥ 1; record in bundle `instance_binding.json` / probe evidence.

## 3. Sample type count (R26)

```bash
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT COUNT(*) FROM seek_production.sample_types;"
```

**Pass:** count ≥ 1 (dev baseline: 104). Fail closed if zero.

## 4. Reference UID for generate-submission

```bash
docker exec seek-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" -N -e \
  "SELECT s.id, am.value FROM seek_production.samples s \
   JOIN seek_production.sample_attribute_values sav ON sav.sample_id=s.id \
   JOIN seek_production.sample_attributes sa ON sa.id=sav.sample_attribute_id AND sa.title='UID' \
   JOIN seek_production.sample_attribute_map am ON am.id=sav.sample_attribute_map_id \
   WHERE am.value='A.ADCD-250312ALT-1-PUB' LIMIT 1;"
```

**Pass:** UID exists **or** catalog/documents signed override per `instance_binding.json`.

## 5. Forbidden actions (Gate 3C harness)

- No `POST /nextseek_api/projects/` in live matrix path
- No `create_seeded_fixture()` when `NEXTSEEK_STEP7_INSTANCE_BINDING=1`
- Validator rejects greenfield `seeded_fixture.json` without `source=instance_binding.json`

## Gate closure

| Milestone | When |
|-----------|------|
| R26-design-ready | This runbook + `instance_binding.json` committed (Gate 3C) |
| R26-closed | Above commands executed on dev; output captured in live bundle (Gate 3D) |
