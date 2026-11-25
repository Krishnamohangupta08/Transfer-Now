"""Single source of truth for the Transfer Now application version.

Every place that needs the version (About dialog, build metadata, Windows
executable version resource, tests, release docs) must import it from here.
``APP_VERSION`` is the human/release version; ``FILE_VERSION`` is the
four-part Windows PE version resource derived from it.
"""

APP_VERSION = "1.0.0.1"

# Windows version resources need exactly four integer parts.
FILE_VERSION = (APP_VERSION.split(".") + ["0", "0", "0"])[:4]
FILE_VERSION_STR = ".".join(FILE_VERSION)
