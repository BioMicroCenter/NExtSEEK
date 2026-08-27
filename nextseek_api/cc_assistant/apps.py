from django.apps import AppConfig


class CcAssistantConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "nextseek_api.cc_assistant"
    label = "cc_assistant"

    def ready(self) -> None:
        from nextseek_api.cc_assistant.step7_llm_cost_ledger import maybe_install_from_env
        maybe_install_from_env()
