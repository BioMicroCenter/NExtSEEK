from chat_nextseek.schemas import WizardAgentOutput


def test_wizard_agent_output_accepts_selection_updates():
    out = WizardAgentOutput(
        action="stay",
        selection_updates={"uids": ["UID1", "UID2"]},
        reply="Loaded 2 samples.",
    )
    assert out.selection_updates == {"uids": ["UID1", "UID2"]}


def test_wizard_agent_output_selection_updates_defaults_to_empty_dict():
    out = WizardAgentOutput(action="stay", reply="hi")
    assert out.selection_updates == {}


def test_wizard_agent_output_advance_with_extracted_still_works():
    out = WizardAgentOutput(
        action="advance",
        extracted={"pipeline": "rnaseq"},
        reply="rnaseq it is.",
    )
    assert out.extracted == {"pipeline": "rnaseq"}
    assert out.selection_updates == {}
