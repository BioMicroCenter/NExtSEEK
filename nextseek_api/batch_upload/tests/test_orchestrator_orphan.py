"""Tests for orphan resolution support in orchestrator."""
from nextseek_api.batch_upload.models import InputRowModel, RowOutcome


class TestBuildIdentityMap:
    def test_builds_from_successful_inserts(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="MUS-260305MIT-1", SampleType="MUS",
                json_metadata='{"UID":"MUS-260305MIT-1","Name":"Mouse-A"}',
            ),
            InputRowModel(
                UID="MUS-260305MIT-2", SampleType="MUS",
                json_metadata='{"UID":"MUS-260305MIT-2","Name":"Mouse-B"}',
            ),
        ]
        outcomes = {
            "MUS-260305MIT-1": RowOutcome(status="success", sample_id=100),
            "MUS-260305MIT-2": RowOutcome(status="failed", reason="DB error"),
        }

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert identity_map["Mouse-A"] == "MUS-260305MIT-1"
        assert "Mouse-B" not in identity_map  # failed insert excluded
        assert parent_info["MUS-260305MIT-1"]["sample_id"] == 100

    def test_file_based_type_uses_file_primary_data(self):
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="D.IMG-260305MIT-1", SampleType="D.IMG",
                json_metadata='{"UID":"D.IMG-260305MIT-1","File_PrimaryData":"image.tiff"}',
            ),
        ]
        outcomes = {"D.IMG-260305MIT-1": RowOutcome(status="success", sample_id=300)}

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert identity_map["image.tiff"] == "D.IMG-260305MIT-1"

    def test_skipped_with_sample_id_included(self):
        """Skipped duplicates with sample_id should be in parent_info."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="NHP-260305MIT-1", SampleType="NHP",
                json_metadata='{"UID":"NHP-260305MIT-1","Name":"Monkey-A"}',
            ),
        ]
        outcomes = {
            "NHP-260305MIT-1": RowOutcome(
                status="skipped", reason="duplicate", sample_id=200
            ),
        }

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert identity_map["Monkey-A"] == "NHP-260305MIT-1"
        assert parent_info["NHP-260305MIT-1"]["sample_id"] == 200

    def test_no_identity_when_name_missing(self):
        """Rows without Name or File_PrimaryData should not appear in identity_map."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="MUS-260305MIT-1", SampleType="MUS",
                json_metadata='{"UID":"MUS-260305MIT-1"}',
            ),
        ]
        outcomes = {"MUS-260305MIT-1": RowOutcome(status="success", sample_id=100)}

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert len(identity_map) == 0
        assert "MUS-260305MIT-1" in parent_info

    def test_first_identity_wins_on_collision(self):
        """If two rows share the same Name, first one wins in identity_map."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="MUS-260305MIT-1", SampleType="MUS",
                json_metadata='{"UID":"MUS-260305MIT-1","Name":"Same"}',
            ),
            InputRowModel(
                UID="MUS-260305MIT-2", SampleType="MUS",
                json_metadata='{"UID":"MUS-260305MIT-2","Name":"Same"}',
            ),
        ]
        outcomes = {
            "MUS-260305MIT-1": RowOutcome(status="success", sample_id=100),
            "MUS-260305MIT-2": RowOutcome(status="success", sample_id=101),
        }

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert identity_map["Same"] == "MUS-260305MIT-1"

    def test_file_based_typo_field_supported(self):
        """File_PrimartyData (legacy typo) should also work."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        input_models = [
            InputRowModel(
                UID="A.GEX-260305MIT-1", SampleType="A.GEX",
                json_metadata='{"UID":"A.GEX-260305MIT-1","File_PrimartyData":"data.fastq"}',
            ),
        ]
        outcomes = {"A.GEX-260305MIT-1": RowOutcome(status="success", sample_id=400)}

        identity_map, parent_info = build_identity_map(input_models, outcomes)

        assert identity_map["data.fastq"] == "A.GEX-260305MIT-1"

    def test_empty_inputs(self):
        """Empty inputs should return empty maps."""
        from nextseek_api.batch_upload.orchestrator import build_identity_map

        identity_map, parent_info = build_identity_map([], {})

        assert identity_map == {}
        assert parent_info == {}
