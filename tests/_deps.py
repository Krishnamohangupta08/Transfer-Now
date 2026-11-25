"""Dependency policy for the Transfer Now test-suite.

One place decides what the suite may skip on, and why:

* a required dependency that is missing      -> explicit skip, reason names it;
* an optional feature dependency that is
  missing (e.g. headless Chrome for the
  engine tests)                              -> explicit skip, reason names it;
* any other failure                          -> an error (never swallowed
                                                into a silent skip).

Every skip reason states WHAT is missing and WHY the test cannot run, plus
the one-time setup that would provide it.  Importing this module is
side-effect free: no QApplication is created and the heavy optional modules
(PyQt6 / selenium / psutil) are only imported lazily, inside the probe
functions, so merely loading the policy never loads the app stack.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import os
import re
import shutil

_CACHE: dict[str, tuple[bool, str]] = {}

# One-time setup hint reused by several reason strings so it stays identical
# everywhere the browser capability is discussed.
_CHROME_SETUP = ("one-time setup: run once on a networked host so Selenium "
                 "Manager caches Chrome-for-Testing, install chrome+chromedriver "
                 "on PATH, or set TN_CHROMEDRIVER to a matching driver binary")


def _importable(name: str, why: str) -> tuple[bool, str]:
    """(available, skip_reason) for an importable module, cached by name.

    A missing OR broken install counts as missing -- the reason says which
    and includes the import error verbatim.  Errors raised later, at use
    time, are never caught here and surface as real test errors.
    """
    if name not in _CACHE:
        try:
            __import__(name)
            _CACHE[name] = (True, "")
        except Exception as exc:  # noqa: BLE001 — missing/broken install; reason says which
            _CACHE[name] = (False,
                            f"{name} not importable ({type(exc).__name__}: {exc}) -- {why}")
    return _CACHE[name]


# ------------------------------------------------------------------ PyQt6
def has_pyqt6() -> bool:
    """True when the Qt UI stack can be imported (all UI tests need it)."""
    return pyqt6_capability()[0]


def pyqt6_skip_reason() -> str:
    return pyqt6_capability()[1]


def pyqt6_capability() -> tuple[bool, str]:
    return _importable("PyQt6", "the Qt UI under test cannot be constructed without it")


# --------------------------------------------------------------- selenium
def has_selenium() -> bool:
    return selenium_capability()[0]


def selenium_skip_reason() -> str:
    return selenium_capability()[1]


def selenium_capability() -> tuple[bool, str]:
    return _importable(
        "selenium",
        "the upload engine drives the site through Selenium WebDriver")


# ----------------------------------------------------------------- psutil
def has_psutil() -> bool:
    return psutil_capability()[0]


def psutil_skip_reason() -> str:
    return psutil_capability()[1]


def psutil_capability() -> tuple[bool, str]:
    return _importable(
        "psutil",
        "process-tree verification (no survivors after quit) needs it; "
        "without it the lifecycle assertions would be vacuous")


# ------------------------------------------------- browser/driver capability
_CHROME: tuple[bool, str] | None = None


def has_chrome_driver_capability() -> bool:
    """True only when a usable headless browser AND matching driver can
    really be launched: selenium importable, and the browser/driver pair
    either on PATH or in Selenium Manager's cache (which is provisioned
    once, online -- a SETUP dependency, not a per-run test dependency).
    A bare browser binary is not enough."""
    return chrome_driver_capability()[0]


def chrome_skip_reason() -> str:
    return chrome_driver_capability()[1]


def chrome_driver_capability() -> tuple[bool, str]:
    global _CHROME
    if _CHROME is None:
        _CHROME = _probe_chrome()
    return _CHROME


def _probe_chrome() -> tuple[bool, str]:
    try:
        selenium_cap = selenium_capability()
        if not selenium_cap[0]:
            return (False, selenium_cap[1] + " -- " + _CHROME_SETUP)
        from selenium.webdriver.common.selenium_manager import SeleniumManager
    except Exception as exc:  # noqa: BLE001 — selenium present but manager unusable: say so
        return False, (f"Selenium Manager unusable ({type(exc).__name__}: {exc}) -- "
                       "engine tests need a real headless browser+driver; " + _CHROME_SETUP)
    if shutil.which("chromedriver") or shutil.which("msedgedriver"):
        if any(shutil.which(b) for b in ("google-chrome", "chromium", "chrome",
                                         "chromium-browser", "msedge")):
            return True, ""
        return False, ("a webdriver is on PATH but no Chrome/Edge browser binary "
                       "is -- engine tests need a real headless browser; " + _CHROME_SETUP)
    try:
        paths = SeleniumManager().binary_paths(["--browser", "chrome"])
        driver_ok = paths.get("driver_path") and os.path.exists(paths["driver_path"])
        browser_ok = paths.get("browser_path") and os.path.exists(paths["browser_path"])
    except Exception as exc:  # noqa: BLE001 — offline with an empty cache: setup needed
        return False, (f"no chromedriver on PATH and Selenium Manager could not "
                       f"provide Chrome+driver ({type(exc).__name__}: {exc}) -- "
                       + _CHROME_SETUP)
    if driver_ok and browser_ok:
        return True, ""
    return False, ("Selenium Manager reports no usable Chrome+chromedriver pair "
                   "(cache empty) -- engine tests need a real headless browser; "
                   + _CHROME_SETUP)


# ----------------------------------------------------- child-probe helpers
_MODULE_NOT_FOUND = re.compile(r"ModuleNotFoundError: No module named '([^']+)'")


def missing_modules_from_traceback(text: str) -> list[str]:
    """Module names a subprocess reported via ModuleNotFoundError.

    Used by tests that spawn child interpreters: the pre-flight probe fails
    on a missing import and the skip reason must NAME the missing module.
    """
    seen: list[str] = []
    for m in _MODULE_NOT_FOUND.finditer(text or ""):
        if m.group(1) not in seen:
            seen.append(m.group(1))
    return seen


def child_missing_skip_reason(missing: list[str], purpose: str) -> str:
    """Skip reason for a pre-flight probe that found missing modules."""
    return ("child interpreter cannot import " + ", ".join(missing)
            + " -- " + purpose + " cannot run without these dependencies")
