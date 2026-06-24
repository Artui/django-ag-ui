from __future__ import annotations

from django.apps import AppConfig


class StoreConfig(AppConfig):
    """Opt-in app providing the reference conversation model + store.

    Add ``"django_ag_ui.contrib.store"`` to ``INSTALLED_APPS`` and run
    ``migrate`` to enable it, then point
    ``DJANGO_AG_UI["CONVERSATION_STORE"]`` at
    ``django_ag_ui.contrib.store.default_conversation_store.DefaultConversationStore``.
    The base package ships no model, so projects that don't opt in get no
    migration.
    """

    name = "django_ag_ui.contrib.store"
    label = "django_ag_ui_store"
    default_auto_field = "django.db.models.BigAutoField"
    verbose_name = "django-ag-ui conversation store"
