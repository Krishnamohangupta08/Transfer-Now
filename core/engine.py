"""TransferNow upload automation (Selenium, headless, Qt-free).

Flow (validated against the live site):

  1. open https://www.transfernow.net/en   (SPA, inputs appear after ~4-6 s)
  2. dismiss the cookie wizard (best effort)
  3. send the file paths to the last ``input[type=file]`` (newline-joined)
  4. click "Create a link"
  5. type the sender email
  6. click "Get a link"
  7. poll the DOM for the ``/dl/<id>`` share link

The engine is a plain class that reports through a callback so it can be
unit-tested without Qt.  ``request_stop()`` is safe to call from any thread
and immediately kills the browser, so a cancel can never hang the UI.
"""
from __future__ import annotations

import contextlib
import math
import os
import random
import re
import subprocess
import tempfile
import threading
import time
import sys

from collections.abc import Callable
from dataclasses import dataclass

from . import limits, procs
from .emailgen import random_email

def _site_url_default() -> str:
    """The upload destination is a security boundary (A4-06/A4-13).

    ``TN_SITE_URL`` exists for source/CI development only: a frozen
    (shipped) build NEVER honors it, so an env var can never point the
    upload engine or its browser automation at an unexpected site.
    """
    env = os.environ.get("TN_SITE_URL")
    if env and not getattr(sys, "frozen", False):
        return env
    return "https://www.transfernow.net/en"


SITE_URL = _site_url_default()


def _env_driver_path() -> str | None:
    """A4-06: the arbitrary-driver-executable override is source/CI-only; a
    frozen build never launches a binary named by an environment variable."""
    return (None if getattr(sys, "frozen", False)
            else os.environ.get("TN_CHROMEDRIVER"))

# Item count (MAX_FILES_PER_TRANSFER, core.limits) and total size are both capped.
MAX_BYTES = 5 * 1024 ** 3

# Cookie-consent policy (privacy rule): this app must NEVER accept — or
# silently waive — any consent on the user's behalf.  Accepting cookies is a
# decision that belongs to the user, not the software.  Only rejection /
# necessary-only choices are eligible for auto-clicking (exact,
# case-insensitive label match); when the page offers none of them, the
# wizard is left untouched for the user to handle manually.
CONSENT_REJECT_LABELS = (
    "Reject all",
    "Refuser tout",
    "Alle ablehnen",
    "Rechazar todo",
    "Rifiuta tutto",
    "Continuer sans accepter",
    "Continue without accepting",
    "Only necessary",
    "Necessary only",
)

# (key, label shown in the UI stepper, progress % when the step starts)
STEPS = [
    ("boot", "Starting secure browser", 5),
    ("open", "Opening TransferNow", 15),
    ("files", "Adding your files", 35),
    ("mode", "Choosing link mode", 48),
    ("email", "Setting sender", 56),
    ("upload", "Uploading & creating link", 62),
    ("done", "Link ready", 100),
]
STEP_PERCENT = {k: p for k, _l, p in STEPS}
STEP_LABEL = {k: l for k, l, _p in STEPS}

# Read the REAL upload progress straight out of the TransferNow page while it
# uploads, so the desktop UI can show the site's own percentage instantly
# instead of a time-based guess.  Returns null when the page exposes nothing
# readable (then the caller falls back to the smooth estimate).
#   -> {pct: number|null, sent: bytes|null, total: bytes|null}
PROGRESS_JS = """
return (() => {
    const pct = {v: null};
    const better = (x) => { if (x !== null && (pct.v === null || x > pct.v)) pct.v = x; };
    const rect = (el) => { try { return el.getBoundingClientRect(); } catch (e) { return {width: 0, height: 0}; } };
    // A) canonical progress bars: role=progressbar first, then styled bars
    for (const el of document.querySelectorAll('[role=\\"progressbar\\"]')) {
        const v = parseFloat(el.getAttribute('aria-valuenow'));
        const r = rect(el);
        if (isFinite(v) && v >= 0 && v <= 100 && r.height > 1) better(v);
    }
    if (pct.v === null) {
        for (const el of document.querySelectorAll('[class*=\"progress\" i], [class*=\"upload\" i]')) {
            if (!el.offsetParent && el.getAttribute('style') === null) continue;
            const m = /width:\\s*([\\d.]+)\\s*%/.exec(el.getAttribute('style') || '');
            if (!m) continue;
            const r = rect(el);
            const v = parseFloat(m[1]);          // ignore 100 %: layout widths lie
            if (isFinite(v) && v >= 0 && v < 100 && r.height >= 2 && r.height <= 64
                    && r.width >= 16) better(v);
        }
    }
    // B) text nodes: a lone percent (\"42%\") and byte counters (\"1.2 GB of 3.4 GB\")
    const mult = {kb: 1024, mb: 1048576, gb: 1073741824, tb: 1099511627776};
    const num = (s) => parseFloat(s.replace(/,(?=\\d{3}\\b)/g, '').replace(',', '.'));
    let sent = null, total = null;
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT, null, false);
    for (let n; (n = walker.nextNode()); ) {
        const t = (n.textContent || '').trim();
        if (!t || t.length > 64) continue;
        const mp = /^(\\d{1,3})(?:[.,]\\d+)?\\s*%$/.exec(t);
        if (mp) {
            const el = n.parentElement;
            const r = el ? rect(el) : {height: 0};
            if (r.height > 0 && r.height < 90) better(parseFloat(mp[1]));
        }
        const ms = /([\\d.,]+)\\s*(KB|MB|GB|TB)\\s*(?:\\/|of|out of|sur|de|von)\\s*([\\d.,]+)\\s*(KB|MB|GB|TB)/i.exec(t);
        if (ms) {
            const s = num(ms[1]) * (mult[ms[2].toLowerCase()] || 1);
            const tt = num(ms[3]) * (mult[ms[4].toLowerCase()] || 1);
            if (isFinite(s) && isFinite(tt) && s >= 0 && tt > 0) {
                sent = Math.max(sent || 0, s); total = Math.max(total || 0, tt);
            }
        }
    }
    if (pct.v === null && sent !== null && total !== null && sent <= total * 1.02)
        pct.v = 100 * sent / total;
    if (pct.v === null && sent === null) return null;
    return {pct: pct.v, sent: sent, total: total};
})();"""


class CancelledError(RuntimeError):
    """The user cancelled the transfer."""


class EngineError(RuntimeError):
    """A readable failure (shown directly to the user)."""


@dataclass
class EngineOptions:
    files: list[str]
    email: str | None = None         # None/"" -> anonymous each run
    human_pace: bool = True


Emit = Callable[[str, object], None]    # event name, payload


class TransferEngine:
    """Runs one upload. Call :meth:`run` on a worker thread."""

    def __init__(self, opts: EngineOptions, emit: Emit) -> None:
        self.opts = opts
        self._emit = emit
        self._stop = threading.Event()
        self._driver = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ public api
    def request_stop(self) -> None:
        """Thread-safe. Flags the run to stop and kills the browser at once."""
        self._stop.set()
        threading.Thread(target=self._kill_driver, daemon=True).start()

    # ----------------------------------------------------------------- worker
    def run(self) -> None:
        started = time.monotonic()
        profile = tempfile.mkdtemp(prefix="transfernow-")
        meta: dict = {"files": [], "total_bytes": 0, "email": None, "link": None,
                      "expiry": "7 days", "elapsed_s": None}
        try:
            self._validate()
            self._step("boot")
            procs.register(None, profile)
            driver = self._make_driver(profile)
            with contextlib.suppress(Exception):   # pid may already be gone
                procs.register(driver.service.process.pid)
            with self._lock:
                self._driver = driver
            if self._stop.is_set():
                raise CancelledError()
            self._pace(0.4, 0.9)

            self._step("open")
            driver.get(SITE_URL)
            if not self._poll(lambda: driver.find_elements("css selector",
                                                            "input[type=file]"), 40):
                raise EngineError("Could not load the TransferNow page. "
                                  "Check your internet connection and try again.")
            self._dismiss_consent(driver)
            self._pace(0.3, 0.7)

            self._step("files")
            files = [os.path.abspath(f) for f in self.opts.files]
            if not self._add_files(driver, files):
                raise EngineError("TransferNow did not accept the file(s).")
            meta["files"] = [{"name": os.path.basename(f), "size": os.path.getsize(f)}
                             for f in files]
            meta["total_bytes"] = sum(f["size"] for f in meta["files"])
            self._pace(0.5, 1.0)

            self._step("mode")
            if not self._click_button(driver, "Create a link", 10):
                raise EngineError("The 'Create a link' option was not found. "
                                  "The TransferNow page may have changed.")
            self._pace(0.5, 1.0)

            self._step("email")
            # No user address configured: fall back to an anonymous,
            # IANA-reserved sender (core.emailgen).  The UI (updated
            # separately) labels this "anonymous sender — reserved domain,
            # not a real mailbox"; the recommended mode is the user's own
            # email ("fixed" sender mode).
            email = (self.opts.email or "").strip() or random_email()
            if not self._fill_email(driver, email):
                raise EngineError("Could not fill in the sender email field.")
            meta["email"] = email
            self._pace(0.4, 0.8)

            self._step("upload")
            if not self._click_button(driver, "Get a link", 10):
                raise EngineError("The 'Get a link' button was not found.")
            link = self._wait_for_link(driver, meta["total_bytes"])
            if not link:
                raise EngineError("The upload did not finish in time. "
                                  "Try again, or try fewer / smaller files.")
            meta["link"] = link
            meta["expiry"] = self._read_expiry(driver)
            meta["elapsed_s"] = round(time.monotonic() - started, 1)
            self._step("done")
            self._emit("success", meta)
        except CancelledError:
            self._emit("failed", "cancelled")
        except Exception as exc:  # noqa: BLE001
            if self._stop.is_set():
                self._emit("failed", "cancelled")
            else:
                msg = str(exc) if isinstance(exc, EngineError) else self._explain(exc)
                self._log("ERROR " + msg)
                self._emit("failed", msg)
        finally:
            self._kill_driver()
            procs.kill_profile(profile)         # stray Chrome helpers + the temp profile
            procs.unregister(None, profile)

    # --------------------------------------------------------------- validate
    def _validate(self) -> None:
        files = self.opts.files
        if not files:
            raise EngineError("No files selected.")
        # Backend enforcement: a direct worker/API call hits the same item cap as the UI.
        try:
            limits.check_item_count(files)
        except ValueError as exc:
            raise EngineError(str(exc)) from exc
        total = 0
        for f in files:
            if not os.path.isfile(f):
                raise EngineError(f"File not found: {os.path.basename(f)}")
            if not os.access(f, os.R_OK):
                raise EngineError(f"Cannot read: {os.path.basename(f)}")
            total += os.path.getsize(f)
        if total > MAX_BYTES:
            raise EngineError("The total size is over the 5 GB free limit.")

    @staticmethod
    def _explain(exc: Exception) -> str:
        text = f"{type(exc).__name__}: {exc}".strip()
        low = text.lower()
        if "cannot find chrome" in low or "no such file" in low and "chrome" in low \
                or "binary" in low and "chrome" in low:
            return ("Google Chrome (or Microsoft Edge) was not found. "
                    "Install Chrome and try again.")
        if "session not created" in low or "this version of chromedriver" in low:
            return ("Chrome and its driver are out of sync. Update Chrome, "
                    "then try again.")
        if "err_internet_disconnected" in low or "err_name_not_resolved" in low \
                or "net::err" in low:
            return "No internet connection."
        first = text.splitlines()[0] if text else "Unknown error"
        return first[:300]

    # ---------------------------------------------------------------- driver
    def _make_driver(self, profile: str):
        from selenium import webdriver

        errors: list[str] = []
        for flavour in ("chrome", "edge"):
            if self._stop.is_set():
                raise CancelledError()
            try:
                return self._start_browser(webdriver, flavour, profile)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{flavour}: {exc}")
                self._log(f"{flavour} failed to start: {str(exc).splitlines()[0][:160]}")
        raise EngineError(
            "Could not start a browser. Install Google Chrome (or Microsoft Edge) "
            "and try again.\n" + errors[0].splitlines()[0][:200])

    def _start_browser(self, webdriver, flavour: str, profile: str):
        if flavour == "chrome":
            from selenium.webdriver.chrome.options import Options
            from selenium.webdriver.chrome.service import Service
            opts, svc_cls, drv_cls = Options(), Service, webdriver.Chrome
        else:
            from selenium.webdriver.edge.options import Options
            from selenium.webdriver.edge.service import Service
            opts, svc_cls, drv_cls = Options(), Service, webdriver.Edge
        # The Chrome sandbox stays ON by default — it is a real security
        # boundary (render-process isolation) and works on normal desktops.
        # Opt out ONLY for containers / CI where it cannot run: explicit
        # TN_CHROME_NO_SANDBOX=1/true, or running as root.  Never a desktop
        # default.
        # A4-06: the env hatch is source/CI-only — a frozen build never
        # drops the Chrome sandbox because of an environment variable
        # (running as root still needs it).
        no_sandbox = ((not getattr(sys, "frozen", False)
                       and os.environ.get("TN_CHROME_NO_SANDBOX", "").strip().lower()
                       in ("1", "true"))
                      or (hasattr(os, "geteuid") and os.geteuid() == 0))
        # Functional flags only: no automation-concealment switches — the
        # browser stays honest about what it is (no bot-protection bypass).
        args = ["--headless=new", "--disable-dev-shm-usage", "--disable-gpu",
                "--no-first-run", "--no-default-browser-check",
                "--disable-extensions", "--disable-background-networking",
                "--lang=en-US", f"--user-data-dir={profile}",
                "--window-size=1440,900"]
        if no_sandbox:
            args.append("--no-sandbox")
        for arg in args:
            opts.add_argument(arg)
        opts.page_load_strategy = "eager"
        driver_path = _env_driver_path()
        service = svc_cls(executable_path=driver_path) if driver_path else svc_cls()
        if os.name == "nt":  # no flashing console window for the driver
            with contextlib.suppress(Exception):
                service.creation_flags = subprocess.CREATE_NO_WINDOW
        driver = drv_cls(options=opts, service=service)
        driver.set_page_load_timeout(60)
        driver.set_script_timeout(30)
        return driver

    def _kill_driver(self) -> None:
        with self._lock:
            driver, self._driver = self._driver, None
        if driver is None:
            return
        proc = None
        with contextlib.suppress(Exception):       # driver may already be dead
            proc = driver.service.process
        pid = getattr(proc, "pid", None)
        t = threading.Thread(target=self._safe_quit, args=(driver,), daemon=True)
        t.start()
        t.join(4)
        procs.kill_tree(pid)                       # always: driver + every Chrome helper
        procs.unregister(pid)

    @staticmethod
    def _safe_quit(driver) -> None:
        with contextlib.suppress(Exception):       # quit is best-effort at teardown
            driver.quit()

    # ------------------------------------------------------------- utilities
    def _step(self, key: str) -> None:
        self._emit("step", (key, STEP_LABEL[key], STEP_PERCENT[key]))
        self._log(STEP_LABEL[key])

    def _log(self, text: str) -> None:
        self._emit("log", f"[{time.strftime('%H:%M:%S')}] {text}")

    def _pace(self, lo: float, hi: float) -> None:
        seconds = random.uniform(lo, hi) if self.opts.human_pace else 0.05
        if self._stop.wait(seconds):
            raise CancelledError()

    def _poll(self, fn: Callable, timeout: float, interval: float = 0.4):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._stop.is_set():
                raise CancelledError()
            try:
                value = fn()
            except Exception:  # noqa: BLE001
                value = None
            if value:
                return value
            if self._stop.wait(interval):
                raise CancelledError()
        return None

    # ----------------------------------------------------------- page actions
    def _dismiss_consent(self, driver) -> None:
        """Best-effort close of the cookie wizard — REJECT-only (see
        CONSENT_REJECT_LABELS).  Privacy rule: this app never accepts cookies
        or any other consent on the user's behalf.  Exact, case-insensitive,
        whitespace-normalised label match on visible buttons/links; when no
        rejection-style control is visible, nothing is clicked and the wizard
        stays for the user to handle manually."""
        for label in CONSENT_REJECT_LABELS:
            try:
                clicked = driver.execute_script("""
                    const wanted = arguments[0].replace(/\\s+/g, ' ').trim().toLowerCase();
                    for (const b of document.querySelectorAll('button, a')) {
                        const r = b.getBoundingClientRect();
                        if (r.width === 0) continue;
                        const t = (b.innerText || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                        if (t === wanted) { b.click(); return true; }
                    }
                    return false;""", label)
            except Exception:  # noqa: BLE001 — page may be mid-navigation
                clicked = False
            if clicked:
                self._pace(0.5, 0.9)
                return

    def _add_files(self, driver, files: list[str]) -> bool:
        """Attach the files and wait for the upload form to appear.

        send_keys to the site's hidden ``input[type=file]`` used to be enough;
        newer Chrome builds and the redesigned welcome page can silently drop
        it (the input's ``files`` list stays empty), so every attempt is now
        VERIFIED and retried through three escalation levels:

          1. plain ``send_keys`` on every file input (last first, as before)
          2. unhide the input, then ``send_keys`` again
          3. CDP ``DOM.setFileInputFiles`` — sets the files at browser level,
             exactly like a real file-picker selection

        If the files are registered but the form still hides behind the new
        welcome screen, the big Start / drop-zone button is clicked too.
        """
        payload = "\n".join(files)
        end = time.monotonic() + 45
        registered = False
        while time.monotonic() < end:
            if self._stop.is_set():
                raise CancelledError()
            if not registered:
                inputs = driver.find_elements("css selector", "input[type=file]")
                for idx in range(len(inputs) - 1, -1, -1):
                    if self._stop.is_set():
                        raise CancelledError()
                    if self._attach(driver, inputs[idx], files, payload):
                        registered = True
                        break
            if registered:
                def form_open():
                    if self._js_button(driver, "Create a link"):
                        return True
                    for el in driver.find_elements("css selector",
                                                   "input[type=email]"):
                        with contextlib.suppress(Exception):   # stale element
                            if el.is_displayed():
                                return True
                    return False

                if self._poll(form_open, 8):
                    return True
                # New welcome page: files stashed but the form waits behind
                # the Start circle — click it, then look for the form again.
                with contextlib.suppress(Exception):   # page may be re-rendering
                    driver.execute_script("""
                        for (const b of document.querySelectorAll('button')) {
                            const r = b.getBoundingClientRect();
                            if (r.width === 0) continue;
                            const t = (b.innerText || '').trim();
                            if (t === 'Start' || t === 'Add files') { b.click(); break; }
                        }""")
            self._pace(0.6, 1.2)
        if not registered:
            self._log("File inputs visible: "
                      f"{self._buttons_summary(driver)}")
        return registered

    def _attach(self, driver, inp, files: list[str], payload: str) -> bool:
        """Try every mechanism to put ``files`` on a file input; return True
        as soon as the DOM really reports them."""
        need = len(files)

        def registered() -> bool:
            try:
                n = driver.execute_script("""
                    let m = 0;
                    for (const i of document.querySelectorAll('input[type=file]'))
                        m = Math.max(m, (i.files || []).length);
                    return m;""")
                return isinstance(n, int) and n >= need
            except Exception:  # noqa: BLE001
                return False

        # 1) plain send_keys (works when the input is stable)
        with contextlib.suppress(Exception):       # stale element — other levels retry
            inp.send_keys(payload)
        if registered():
            return True
        # 2) some builds refuse display:none inputs — reveal and retry
        with contextlib.suppress(Exception):       # stale element — CDP level retries
            driver.execute_script(
                "const i = arguments[0]; i.removeAttribute('class');"
                "i.style.cssText='position:fixed;top:0;left:0;width:8px;"
                "height:8px;opacity:0.01;z-index:9999';", inp)
            inp.send_keys(payload)
        if registered():
            return True
        # 3) CDP — browser-level, immune to framework re-renders
        with contextlib.suppress(Exception):   # not every flavour of driver has CDP
            doc = driver.execute_cdp_cmd("DOM.getDocument", {"depth": -1})
            q = driver.execute_cdp_cmd("DOM.querySelectorAll", {
                "nodeId": doc["root"]["nodeId"],
                "selector": "input[type=file]"})
            if q.get("nodeIds"):
                driver.execute_cdp_cmd("DOM.setFileInputFiles", {
                    "files": list(files), "nodeId": q["nodeIds"][-1]})
        if registered():
            return True
        # 4) files may be set but the framework missed the events
        with contextlib.suppress(Exception):   # detached frame etc. — final check decides
            driver.execute_script("""
                const i = [...document.querySelectorAll('input[type=file]')].pop();
                if (i) {
                    i.dispatchEvent(new Event('input', {bubbles: true}));
                    i.dispatchEvent(new Event('change', {bubbles: true}));
                }""")
        return registered()

    @staticmethod
    def _norm(text: str) -> str:
        """Collapse every whitespace flavour (incl. NBSP) into single spaces."""
        return " ".join((text or "").replace("\u00a0", " ").split()).strip()

    @classmethod
    def _text_matches(cls, text: str, label: str) -> bool:
        """Exact label, or the label followed by a decorative suffix.

        The live CTA now renders e.g. "Get a link \u00b7 3 files \u00b7 3.9 MB"
        (TransferNow appends the file count + total size to the button), so an
        exact match broke real uploads with "The 'Get a link' button was not
        found."  The suffix always starts with a separator glyph (\u00b7, -, :,
        \u2026) \u2014 anything else ("Get a link with Premium\u2026") is a different
        action and must NOT match.
        """
        t = cls._norm(text)
        if t == label:
            return True
        if not t.startswith(label):
            return False
        rest = t[len(label):].lstrip()
        return bool(rest) and len(rest) <= 40 and rest[0] in "\u00b7\u2022\u2013\u2014-(:"

    @staticmethod
    def _js_button(driver, label: str):
        return driver.execute_script("""
            const norm = s => (s || '').replace(/\\u00a0/g, ' ')
                .split(/\\s+/).filter(Boolean).join(' ');
            const label = arguments[0];
            const SEP = '\\u00b7\\u2022\\u2013\\u2014-(:';
            let partial = null;
            for (const b of document.querySelectorAll('button')) {
                const r = b.getBoundingClientRect();
                if (r.width === 0 || r.height === 0) continue;
                const t = norm(b.innerText);
                if (t === label) return b;
                if (!partial && t.startsWith(label)) {
                    const rest = t.slice(label.length).trimStart();
                    if (rest.length > 0 && rest.length <= 40 && SEP.includes(rest[0]))
                        partial = b;
                }
            }
            return partial;""", label)

    @staticmethod
    def _buttons_summary(driver, limit: int = 10) -> str:
        """One-line list of visible buttons, for failure diagnostics."""
        try:
            labels = driver.execute_script("""
                const out = [];
                for (const b of document.querySelectorAll('button, [role=button]')) {
                    const r = b.getBoundingClientRect();
                    if (r.width === 0 || r.height === 0) continue;
                    const t = (b.innerText || '').replace(/\\s+/g, ' ').trim();
                    if (t) out.push(t.slice(0, 32));
                }
                return out.slice(0, arguments[0]);""", limit) or []
        except Exception:  # noqa: BLE001
            return "(page unreadable)"
        return " | ".join(labels) if labels else "(none)"

    def _click_button(self, driver, label: str, timeout: float) -> bool:
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._stop.is_set():
                raise CancelledError()
            btn = self._js_button(driver, label)
            if btn:
                with contextlib.suppress(Exception):   # re-rendered mid-click; retry
                    driver.execute_script("arguments[0].click();", btn)
                    return True
            if self._stop.wait(0.4):
                raise CancelledError()
        self._log(f"'{label}' not found. Visible buttons: "
                  f"{self._buttons_summary(driver)}")
        return False

    def _fill_email(self, driver, email: str) -> bool:
        def visible_email():
            # 1) dedicated sender-email widget of the redesigned form, then
            # 2) any email-typed field, then 3) any field that mentions email
            for sel in ("#uploaderSenderEmail input", "input[type=email]",
                        "input[name*='email' i]", "input[id*='email' i]",
                        "input[placeholder*='email' i]"):
                for el in driver.find_elements("css selector", sel):
                    with contextlib.suppress(Exception):   # stale element
                        if el.is_displayed():
                            return el
            return None

        el = self._poll(visible_email, 12)
        if not el:
            return False

        def value_matches() -> bool:
            value = driver.execute_script("return arguments[0].value;", el) or ""
            return value.strip() == email

        for round_ in (1, 2):
            try:
                el.click()
                with contextlib.suppress(Exception):   # some inputs refuse clear()
                    el.clear()
                if self.opts.human_pace:
                    for ch in email:
                        el.send_keys(ch)
                        if self._stop.wait(random.uniform(0.02, 0.06)):
                            raise CancelledError()
                else:
                    el.send_keys(email)
            except CancelledError:
                raise
            except Exception:  # noqa: BLE001, S110 — typing failed; value check decides
                pass
            if value_matches():
                return True
            if round_ == 1:  # framework swallowed the typing — set it natively
                driver.execute_script("""
                    const el = arguments[0];
                    const set = Object.getOwnPropertyDescriptor(
                        HTMLInputElement.prototype, 'value').set;
                    el.focus(); set.call(el, arguments[1]);
                    el.dispatchEvent(new Event('input', {bubbles: true}));
                    el.dispatchEvent(new Event('change', {bubbles: true}));
                    el.blur();""", el, email)
                self._stop.wait(0.3)
        return value_matches()

    def _page_progress(self, driver) -> dict | None:
        """Real progress scraped from the page (None when not exposed)."""
        try:
            raw = driver.execute_script(PROGRESS_JS)
        except Exception:  # noqa: BLE001 — navigation, detached frame, …
            return None
        return raw if isinstance(raw, dict) else None

    def _wait_for_link(self, driver, total_bytes: int) -> str | None:
        timeout = int(min(1800, 120 + total_bytes / (1024 * 1024) * 4))
        t0 = time.monotonic()
        last_pct = -1
        last_sent = -1
        while time.monotonic() - t0 < timeout:
            if self._stop.is_set():
                raise CancelledError()
            link = self._find_link(driver)
            if link:
                return link
            # 1) the site's own progress, extracted live from the DOM
            data = self._page_progress(driver)
            pct = sent = None
            if data:
                if isinstance(data.get("pct"), (int, float)) and 0 <= data["pct"] <= 100:
                    pct = min(int(data["pct"]), 98)   # 100 only when the link is back
                if isinstance(data.get("sent"), (int, float)) and data.get("total"):
                    sent = int(min(data["sent"], total_bytes))
                    if sent > last_sent:
                        last_sent = sent
                        self._emit("bytes", (sent, total_bytes))
                    if pct is None and total_bytes:
                        pct = min(98, int(100 * sent / total_bytes))
            # 2) fallback: smooth, honest estimate while the page hides its bar
            if pct is None:
                elapsed = time.monotonic() - t0
                pct = 62 + int(34 * (1 - math.exp(-elapsed / max(20.0, timeout * 0.22))))
            pct = max(pct, last_pct)              # never slide backwards
            if pct != last_pct:
                last_pct = pct
                self._emit("progress", pct)
            if self._stop.wait(0.4):
                raise CancelledError()
        return None

    @staticmethod
    def _find_link(driver) -> str | None:
        try:
            found = driver.execute_script("""
                const re = /https:\\/\\/www\\.transfernow\\.net\\/(?:en\\/)?dl\\/[A-Za-z0-9-]+/;
                for (const i of document.querySelectorAll('input')) {
                    const m = re.exec(i.value || ''); if (m) return m[0];
                }
                for (const a of document.querySelectorAll('a[href]')) {
                    const m = re.exec(a.href || ''); if (m) return m[0];
                }
                const t = (document.body.innerText || '').match(re);
                return t ? t[0] : null;""")
            return found if isinstance(found, str) else None
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _read_expiry(driver) -> str:
        try:
            txt = driver.execute_script("""
                const els = [...document.querySelectorAll('div,section,p,span')]
                    .filter(e => /available for|link is ready/i.test(e.innerText || ''));
                return els.length ? els[els.length - 1].innerText : '';""") or ""
            m = re.search(r"available for\s+(\d+)\s*days", txt, re.IGNORECASE) \
                or re.search(r"\b(\d{1,3})\s*days\b", txt)
            return f"{m.group(1)} days" if m else "7 days"
        except Exception:  # noqa: BLE001
            return "7 days"
