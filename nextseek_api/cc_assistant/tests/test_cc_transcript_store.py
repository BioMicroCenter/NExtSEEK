def test_ccsessiontranscript_model_shape():
    """Field set + db_table guard — does not touch the DB (no migrate/connect)."""
    import django
    from django.conf import settings
    if not settings.configured:
        settings.configure(
            INSTALLED_APPS=[], DATABASES={}, USE_TZ=True,
        )
        django.setup()
    from nextseek_api.assistant.models_db import CCSessionTranscript
    names = {f.name for f in CCSessionTranscript._meta.get_fields()}
    assert {"chat_session", "cc_session_id", "turn_id", "blob",
            "uncompressed_size", "created_at"} <= names
    assert CCSessionTranscript._meta.db_table == "assistant_cc_transcript"
