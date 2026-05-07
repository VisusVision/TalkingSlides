"""AppConfig for the core app.

The ``name`` attribute MUST match the dotted module path used in
``INSTALLED_APPS`` (``"api.core"``), which in turn requires that the repo
root (``/app``) is on ``PYTHONPATH`` so Python can resolve the ``api``
package.  The ``default_app_config`` in ``api/core/__init__.py`` is not
needed for Django >= 3.2; the AppConfig is discovered automatically.
"""

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    # Must equal the INSTALLED_APPS entry: "api.core"
    name = "core"
    verbose_name = "AI Academy Core"
