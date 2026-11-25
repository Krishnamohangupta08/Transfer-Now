"""Network policy for Transfer Now 1.0.2 — the single source of truth for
every outbound connection the app makes.

Three layers of defense live here (M1/M2/M3 of the release hardening):

  * **M1 destination validation** — :func:`validate_outbound_url` parses a URL
    and enforces scheme, allowlisted hostnames, no embedded credentials,
    no literal-IP / private / loopback / link-local targets and sane ports
    *before* any socket is opened.
  * **M2 test-mode isolation** — :func:`is_test_mode` is the ONLY switch that
    relaxes the address-range checks (loopback + private, for the offline
    test-suite's 127.0.0.1 mock servers).  It requires a truthy
    ``TN_TEST_LOCAL_HTTP`` environment variable AND a non-frozen build; a
    PyInstaller ``--onefile`` executable ignores the variable entirely.
  * **M3 DNS pinning** — :func:`resolve_and_pin` resolves a validated host
    and requires *every* answer to be public (defeating DNS rebinding), and
    :class:`PinnedSession` then connects to the validated IP while keeping
    TLS SNI, certificate hostname verification (verify stays ON) and the
    ``Host`` header on the real hostname.  The pinned connect uses a raw
    ``socket.socket().connect()`` on the validated literal, so no resolver
    is consulted on the wire path and a rebinding answer that arrives after
    validation cannot change where the bytes go.

Redirect handling (:func:`follow_redirects`) is a manual walker: each hop's
``Location`` is resolved and re-validated with the same rules, hops are
budgeted, loops are detected, and cross-origin hops strip ``Cookie`` /
``Authorization`` headers.

Nothing in this module ever puts a full URL (signed query strings,
userinfo) into an exception message — use :func:`redact` when a URL has to
appear in a message or log.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import sys
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import requests
from requests.adapters import HTTPAdapter
from urllib3 import PoolManager
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

__all__ = [
    "NetworkPolicyError",
    "ParsedTarget",
    "PinnedTarget",
    "is_test_mode",
    "validate_outbound_url",
    "resolve_and_pin",
    "assert_public_answers",
    "PinnedSession",
    "follow_redirects",
    "redact",
    "TEST_LOCAL_HTTP_ENV",
    "ALLOWED_PAGE_HOSTS",
    "ALLOWED_STORAGE_HOSTS",
    "TN_FAMILY_SUFFIX",
    "REDIRECT_STATUS",
]

#: Environment variable that enables the offline-test escape hatch.  Truthy
#: values are "1", "true" and "yes" (case-insensitive).  It is read at call
#: time and is NEVER set by the shipped application; frozen builds ignore it.
TEST_LOCAL_HTTP_ENV = "TN_TEST_LOCAL_HTTP"

#: Share pages live exactly here (no subdomain tricks — "www2." and friends
#: are rejected).  Moved here from downloader.py, which re-imports them for
#: backwards compatibility.
ALLOWED_PAGE_HOSTS = {"www.transfernow.net", "transfernow.net"}

#: Signed storage URL hosts the downloads API is known to return.  The real
#: CDN hostname is not documented and may change, so the ``*.transfernow.net``
#: family suffix is an explicitly documented escape hatch.
ALLOWED_STORAGE_HOSTS = {
    "download.transfernow.net",
    "storage.transfernow.net",
    "files.transfernow.net",
    "cdn.transfernow.net",
}
TN_FAMILY_SUFFIX = ".transfernow.net"

#: Hostnames that identify the offline test harness's mock servers.
_LOCAL_TEST_HOSTS = {"127.0.0.1", "localhost"}

#: Status codes the redirect walker treats as a hop.
REDIRECT_STATUS = (301, 302, 303, 307, 308)

_TRUTHY = {"1", "true", "yes"}


class NetworkPolicyError(ValueError):
    """A URL / host / address violated the outbound network policy."""


@dataclass(frozen=True)
class ParsedTarget:
    """A URL that passed M1 destination validation (no DNS resolution yet)."""
    scheme: str            # "https", or "http" only where the policy allows
    host: str              # validated address literal or lowercased hostname
    port: int              # explicit port, or the scheme default
    url: str               # the URL as validated (query string intact)
    hostname: str          # lowercased hostname ("" for literal-IP URLs)

    @property
    def is_literal_ip(self) -> bool:
        return not self.hostname


@dataclass(frozen=True)
class PinnedTarget:
    """A validated host plus the concrete public address to connect to.

    ``addr`` is what the transport MUST connect to (M3); ``hostname`` stays
    the TLS SNI / certificate / ``Host``-header identity.
    """
    host: str              # hostname (or literal) as resolved
    port: int
    hostname: str          # "" when the URL itself used a literal IP
    addr: str              # the pinned address literal to connect to
    family: int            # socket.AF_INET / socket.AF_INET6
    answers: tuple = ()    # every address the resolver returned


# ----------------------------------------------------------------------- M2

def is_test_mode() -> bool:
    """True when the offline-test escape hatch is explicitly enabled.

    The variable must be truthy ("1"/"true"/"yes", case-insensitive) AND the
    interpreter must not run a frozen (PyInstaller) build — a shipped
    executable never honors ``TN_TEST_LOCAL_HTTP``, so a user-set variable
    can never relax the SSRF protections of a released app.
    """
    if getattr(sys, "frozen", False):
        return False
    return os.environ.get(TEST_LOCAL_HTTP_ENV, "").strip().lower() in _TRUTHY


def _banned_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Any address an outbound connection must never talk to (SSRF guard)."""
    return (ip.is_private or ip.is_loopback or ip.is_link_local
            or ip.is_reserved or ip.is_multicast or ip.is_unspecified)


def _effective_ip(ip):
    """Unwrap v4-mapped / 6to4 IPv6 so the *inner* address is judged."""
    if isinstance(ip, ipaddress.IPv6Address) and (ip.ipv4_mapped or ip.sixtofour):
        return ipaddress.ip_address(ip.ipv4_mapped or ip.sixtofour)
    return ip


def _loopback_relaxed(relax: bool, eff_ip) -> bool:
    """The offline-harness escapes relax ONLY the loopback rejection (the
    mock servers bind 127.0.0.1 / ::1).  Private / link-local / reserved
    answers stay rejected even in test mode — the flag must never widen the
    reachable surface beyond localhost."""
    return relax and eff_ip.is_loopback


def _relaxed_destination(test: bool, harness: bool, eff_ip,
                         loopback_name: bool) -> bool:
    """Whether THIS destination is admitted by one of the offline escapes.

    Test mode (``TN_TEST_LOCAL_HTTP`` on a non-frozen build) admits any
    loopback address and localhost-style names.  The ``harness`` escape (the
    *direct* target of ``download()``, whose URL normally comes from the
    strict ``resolve_download_url`` policy) admits exactly ``127.0.0.1`` and
    localhost names, so the offline mock servers stay reachable without the
    flag.  Neither escape ever admits private / link-local ranges — those
    stay rejected everywhere.
    """
    if loopback_name:
        return test or harness
    if eff_ip is not None and eff_ip.is_loopback:
        if test:
            return True                     # test mode: any loopback address
        return harness and str(eff_ip) == "127.0.0.1"
    return False


# ----------------------------------------------------------------------- M1

def validate_outbound_url(url: str, *, purpose: str,
                          https_required: bool = True,
                          harness: bool = False) -> ParsedTarget:
    """Validate a URL against the outbound policy; return a ParsedTarget.

    ``purpose`` selects the trust rules: "page"/"linkcheck" allowlist
    :data:`ALLOWED_PAGE_HOSTS`; "storage" allowlists
    :data:`ALLOWED_STORAGE_HOSTS` plus any ``*.transfernow.net`` subdomain.

    ``harness`` is the documented offline-harness escape used only for the
    *direct* target of :func:`core.downloader.download`: loopback names and
    the 127.0.0.1 literal with any port stay reachable.  Redirect hops and
    every other caller never set it.

    Checks (fail closed): parses with an http(s) scheme and a host; no
    embedded credentials; https unless test-mode/harness loopback; literal
    IPs never allowlisted (v4-mapped/6to4 unwrapped; loopback/private only
    via test mode or the harness escape); named hosts trusted for the
    purpose; private/reserved/loopback/link-local addresses (169.254/16,
    fe80::/10, ::ffff:private) rejected outside those escapes; explicit
    ports only on those loopback escapes.  Raises
    :class:`NetworkPolicyError`; messages never contain the URL itself —
    use :func:`redact` for a safe location reference.
    """
    if purpose not in ("page", "storage", "linkcheck"):
        raise NetworkPolicyError(f"unknown validation purpose {purpose!r}")
    if not isinstance(url, str) or not url.strip():
        raise NetworkPolicyError("empty URL")
    try:
        parts = urlsplit(url.strip())
    except ValueError as exc:
        raise NetworkPolicyError("malformed URL") from exc
    scheme = (parts.scheme or "").lower()
    host_raw = parts.hostname or ""
    host = host_raw.lower()
    if scheme not in ("http", "https"):
        raise NetworkPolicyError("unsupported URL scheme")
    if not host:
        raise NetworkPolicyError("URL has no host")
    if parts.username is not None or parts.password is not None:
        raise NetworkPolicyError("URL contains embedded credentials")
    test = is_test_mode()
    try:
        port = parts.port
    except ValueError as exc:
        raise NetworkPolicyError("invalid port") from exc
    if port is None:
        port = 443 if scheme == "https" else 80
    if not 0 < port < 65536:
        raise NetworkPolicyError("invalid port")
    default_port = 443 if scheme == "https" else 80
    try:
        ip = ipaddress.ip_address(host_raw)
    except ValueError:
        ip = None
    if ip is not None:
        # A literal IP can never be allowlisted: only the offline-test
        # hatch (or the download() harness escape) admits loopback ones —
        # never private / link-local ranges.
        eff = _effective_ip(ip)
        relaxed = _relaxed_destination(test, harness, eff, False)
        if not relaxed:
            raise NetworkPolicyError(
                "literal IP address is not an allowed destination")
        if https_required and scheme != "https" and not relaxed:
            raise NetworkPolicyError("URL is not https")
        if port != default_port and not relaxed:
            raise NetworkPolicyError("unexpected port")
        return ParsedTarget(scheme=scheme, host=str(ip), port=port,
                            url=url.strip(), hostname="")
    trusted = (host in ALLOWED_PAGE_HOSTS if purpose in ("page", "linkcheck")
               else host in ALLOWED_STORAGE_HOSTS
               or host.endswith(TN_FAMILY_SUFFIX))
    loopback_name = host in _LOCAL_TEST_HOSTS or host.endswith(".localhost")
    relaxed = _relaxed_destination(test, harness, None, loopback_name)
    if not (trusted or relaxed):
        raise NetworkPolicyError("host is not trusted for this request")
    if https_required and scheme != "https" and not relaxed:
        raise NetworkPolicyError("URL is not https")
    if port != default_port and not relaxed:
        raise NetworkPolicyError("unexpected port")
    return ParsedTarget(scheme=scheme, host=host, port=port,
                        url=url.strip(), hostname=host)


# ----------------------------------------------------------------------- M3

def resolve_and_pin(host: str, port: int, *,
                    allow_loopback: bool = False) -> PinnedTarget:
    """Resolve ``host`` and return the pinned public address to connect to.

    Every address the resolver returns must be public: a single private /
    loopback / link-local answer rejects the whole host — a name alternating
    public and private answers (DNS rebinding) is caught here.  The first
    public address becomes the pin; it cannot be changed afterwards because
    :class:`PinnedSession` connects to ``addr`` with a raw socket, with no
    further resolver involvement.

    ``host`` may itself be a validated literal (no resolver round-trip).
    ``allow_loopback`` is the documented offline-harness escape used by
    :func:`core.downloader.download` for its *direct* target only (the
    shipped app never produces such a URL); redirect hops never set it.
    Raises :class:`NetworkPolicyError` for empty answer sets,
    ``socket.gaierror``-style resolution failures and any banned answer
    (outside test mode / the harness escape; both relax the address-range
    rejection for loopback/private answers only — scheme, allowlist and
    link-local rules stay enforced).
    """
    relax = is_test_mode() or allow_loopback
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        eff = _effective_ip(ip)
        if not _loopback_relaxed(relax, eff) and _banned_ip(eff):
            raise NetworkPolicyError(
                "literal IP address is not an allowed destination")
        return PinnedTarget(host=str(ip), port=port, hostname="",
                            addr=str(ip),
                            family=socket.AF_INET6 if ip.version == 6
                            else socket.AF_INET,
                            answers=(str(ip),))
    try:
        infos = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
    except (socket.gaierror, OSError, UnicodeError) as exc:
        raise NetworkPolicyError("host could not be verified") from exc
    answers: list[str] = []
    pinned: str | None = None
    family = 0
    for af, _socktype, _proto, _canon, sockaddr in infos:
        try:
            a = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        eff = _effective_ip(a)
        if not _loopback_relaxed(relax, eff) and _banned_ip(eff):
            raise NetworkPolicyError(
                "host resolves to a private or unresolvable address")
        answers.append(str(a))
        if pinned is None:
            pinned, family = str(a), af
    if pinned is None:
        raise NetworkPolicyError(
            "host resolves to a private or unresolvable address")
    return PinnedTarget(host=host, port=port, hostname=host, addr=pinned,
                        family=family, answers=tuple(answers))


# ------------------------------------------------------------ rebinding

def assert_public_answers(addrs) -> None:
    """Fail closed: EVERY resolved address must be public (rebinding guard).

    Accepts address strings or :class:`ipaddress.IPv4Address` /
    :class:`ipaddress.IPv6Address` objects (the shape the patchable
    ``_resolve_host_ips`` seam returns).  A single private / loopback /
    link-local / reserved / multicast / unspecified answer rejects the whole
    set — a name alternating public and private answers (DNS rebinding)
    cannot slip through.  Unlike :func:`resolve_and_pin` this never relaxes
    anything: it is the strict judgement applied to named hosts on
    production paths.
    """
    for raw in addrs:
        try:
            ip = (raw if isinstance(raw, (ipaddress.IPv4Address,
                                          ipaddress.IPv6Address))
                  else ipaddress.ip_address(str(raw)))
        except ValueError as exc:
            raise NetworkPolicyError(
                "host resolved to an unparseable address") from exc
        eff = _effective_ip(ip)
        if _banned_ip(eff):
            raise NetworkPolicyError(
                "host resolves to a private or unresolvable address")


# ------------------------------------------------------------ pinned socket

def _connect_pinned(addrs: tuple, port: int, timeout, source_address):
    """Open a TCP socket to one of the pre-validated literals — no DNS here.

    The addresses are tried in resolver order (like ``socket.create_connection``
    would): every one of them already passed the public-only validation, so a
    dual-stack name (or ``localhost`` answering both ::1 and 127.0.0.1) still
    connects, but a patched/global resolver can never redirect the connect.
    """
    last: OSError | None = None
    for addr in addrs:
        af = socket.AF_INET6 if ":" in addr else socket.AF_INET
        sock = socket.socket(af, socket.SOCK_STREAM)
        t = timeout
        connect_timeout = getattr(t, "connect_timeout", None)
        if connect_timeout is not None:
            t = connect_timeout
        if t is not None:
            sock.settimeout(t)
        if source_address:
            sock.bind(source_address)
        try:
            sock.connect((addr, port))
        except OSError as exc:
            sock.close()
            last = exc
            continue
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:  # pragma: no cover — some platforms lack TCP_NODELAY
            pass
        sys.audit("http.client.connect", None, addr, port)
        return sock
    raise last if last is not None else OSError(
        "no validated address to connect to")


class _PinnedHTTPConnection(HTTPConnection):
    """HTTP connection whose TCP target is a pre-validated IP (no DNS)."""

    def __init__(self, *a, pinned_addrs: tuple = (), **kw):
        super().__init__(*a, **kw)
        self._pinned_addrs = pinned_addrs

    def _new_conn(self):
        if not self._pinned_addrs:
            return super()._new_conn()
        return _connect_pinned(self._pinned_addrs, self.port,
                               self.timeout, self.source_address)


class _PinnedHTTPSConnection(HTTPSConnection):
    """HTTPS connection whose TCP target is a pre-validated IP.

    Only the TCP destination changes: urllib3's stock ``connect()`` still
    performs the TLS handshake with ``server_hostname`` = the real hostname,
    so SNI and certificate hostname verification (verify ON) are preserved.
    """

    def __init__(self, *a, pinned_addrs: tuple = (), **kw):
        super().__init__(*a, **kw)
        self._pinned_addrs = pinned_addrs

    def _new_conn(self):
        if not self._pinned_addrs:
            return super()._new_conn()
        return _connect_pinned(self._pinned_addrs, self.port,
                               self.timeout, self.source_address)


def _pinned_pool_cls(base_pool, conn_cls, pinned_addrs):
    """A connection-pool class whose connections pin to ``pinned_addrs``."""

    class _PinnedPool(base_pool):
        ConnectionCls = conn_cls

        def _new_conn(self, **kw):  # noqa: ARG002 — urllib3 API shape
            self.num_connections += 1
            return self.ConnectionCls(
                host=self.host, port=self.port,
                timeout=self.timeout.connect_timeout,
                pinned_addrs=pinned_addrs, **self.conn_kw)

    return _PinnedPool


class _PinnedPoolManager(PoolManager):
    """PoolManager that builds the pinned pool classes per scheme."""

    def __init__(self, *a, pool_classes=None, **kw):
        super().__init__(*a, **kw)
        if pool_classes:
            self.pool_classes_by_scheme = pool_classes


class _PinnedAdapter(HTTPAdapter):
    """Adapter mounting pinned pools; proxies are refused (they would
    bypass the pin and the destination validation)."""

    def __init__(self, pool_classes, **kw):
        self._pool_classes = pool_classes
        super().__init__(**kw)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kw):
        pool_kw.setdefault("socket_options",
                           HTTPConnection.default_socket_options)
        self.pinned_poolmanager = _PinnedPoolManager(
            num_pools=connections, maxsize=maxsize, block=block,
            pool_classes=self._pool_classes, **pool_kw)
        self.poolmanager = self.pinned_poolmanager
        return self.poolmanager

    def proxy_manager_for(self, *a, **kw):  # pragma: no cover — refused
        raise NetworkPolicyError(
            "proxies are not allowed on pinned sessions")


class PinnedSession(requests.Session):
    """A :class:`requests.Session` that connects to a validated IP only.

    ``PinnedSession(hostname, port, allow_loopback=False)`` resolves once via
    :func:`resolve_and_pin` (every answer must be public — loopback/private
    are admitted only in test mode or via the harness escape) and mounts
    adapters whose connections open the TCP stream to ``pinned.addr`` while
    TLS SNI, certificate hostname verification (verify stays ON) and the
    HTTP ``Host`` header keep using the real hostname.  Environment proxies
    are ignored (``trust_env = False``): a proxy hop would bypass both the
    pin and the destination validation.

    Per-request URLs are still policy-checked: every request's host must
    equal the pinned hostname (or its literal address) and its port must
    match, otherwise :class:`NetworkPolicyError` is raised before any
    socket is touched.  Redirects are never followed implicitly.
    """

    def __init__(self, hostname: str, port: int,
                 *, allow_loopback: bool = False):
        super().__init__()
        self.trust_env = False          # never route via env proxies
        self.pinned = resolve_and_pin(hostname, port,
                                      allow_loopback=allow_loopback)
        self._hostname = (self.pinned.hostname or hostname).lower()
        self._port = port
        self.mount("https://", _PinnedAdapter({
            "https": _pinned_pool_cls(HTTPSConnectionPool,
                                      _PinnedHTTPSConnection,
                                      self.pinned.answers)}))
        self.mount("http://", _PinnedAdapter({
            "http": _pinned_pool_cls(HTTPConnectionPool,
                                     _PinnedHTTPConnection,
                                     self.pinned.answers)}))

    def request(self, method, url, *a, **kw):  # noqa: D102
        try:
            parts = urlsplit(url if "://" in url else f"https://{url}")
        except ValueError as exc:
            raise NetworkPolicyError("malformed URL") from exc
        host = (parts.hostname or "").lower()
        try:
            port = parts.port
        except ValueError as exc:
            raise NetworkPolicyError("invalid port") from exc
        if not host or (host != self._hostname and host != self.pinned.addr
                        and host not in self.pinned.answers):
            raise NetworkPolicyError(
                "request host does not match the pinned target")
        if port is not None and port != self._port:
            raise NetworkPolicyError(
                "request port does not match the pinned target")
        kw.setdefault("allow_redirects", False)
        return super().request(method, url, *a, **kw)


# ----------------------------------------------------------------- redirects

def redact(url: str) -> str:
    """``scheme://host/path`` — no query string, no userinfo, no port.

    The safe form for logs and error messages: signed storage URLs carry
    their credentials in the query string, which must never be printed.
    Unparsable input degrades to ``"<invalid-url>"``.
    """
    try:
        parts = urlsplit(url or "")
        scheme = (parts.scheme or "").lower()
        netloc = parts.netloc
        if "@" in netloc:                       # drop userinfo
            netloc = netloc.rsplit("@", 1)[1]
        host = (parts.hostname or "").lower()
        if not scheme or not host:
            return "<invalid-url>"
        tail = netloc.rsplit("]", 1)[-1]
        if tail.count(":") == 1:                 # drop an explicit port
            netloc = netloc.rsplit(":", 1)[0]
        return f"{scheme}://{netloc}{parts.path or '/'}"
    except ValueError:
        return "<invalid-url>"


def follow_redirects(fetch, url, *, purpose: str, max_hops: int = 5,
                     https_required: bool = True,
                     headers=None) -> requests.Response:
    """Walk redirects manually with per-hop policy validation.

    ``fetch`` is a callable ``(url) -> requests.Response`` — or
    ``(url, headers) -> Response`` when the ``headers`` argument is supplied
    — and MUST issue its request with ``allow_redirects=False`` (e.g. a
    :class:`PinnedSession` ``get``).  The initial URL is validated for
    ``purpose``; for every 301/302/303/307/308 response the ``Location``
    header is parsed (absolute, or relative and resolved against the current
    URL) and re-validated with the same rules: no downgrade to http, no
    embedded credentials, no untrusted host, no loopback / private /
    link-local target outside test mode.  A missing or malformed
    ``Location``, a hop beyond ``max_hops``, and a hop back to an
    already-visited URL are all errors.

    Cross-origin hops (host change) strip ``Cookie`` / ``Authorization``
    from the headers carried forward; other header decisions (Referer,
    Range …) stay with the caller.

    Non-final responses are ALWAYS closed, including on every error path.
    Returns the final (non-redirect) response — the caller owns it.
    """
    target = validate_outbound_url(url, purpose=purpose,
                                   https_required=https_required).url
    visited = {target}
    current_headers = dict(headers) if headers else None
    current_host = (urlsplit(target).hostname or "").lower()
    resp = None
    try:
        for _hop in range(max_hops + 1):
            resp = (fetch(target, current_headers)
                    if current_headers is not None else fetch(target))
            if resp.status_code not in REDIRECT_STATUS:
                return resp
            location = (resp.headers.get("Location") or "").strip()
            try:
                resp.close()
            except Exception:  # noqa: BLE001, S110 — cleanup never raises
                pass
            resp = None
            if not location:
                raise NetworkPolicyError("redirect without a Location header")
            try:
                nxt = urljoin(target, location)
            except ValueError as exc:
                raise NetworkPolicyError(
                    "malformed redirect Location") from exc
            parsed = validate_outbound_url(nxt, purpose=purpose,
                                           https_required=https_required)
            if parsed.url in visited:
                raise NetworkPolicyError("redirect loop detected")
            visited.add(parsed.url)
            nxt_host = (urlsplit(parsed.url).hostname or "").lower()
            if current_headers is not None and nxt_host != current_host:
                current_headers = {
                    k: v for k, v in current_headers.items()
                    if k.lower() not in ("cookie", "authorization")}
            current_host = nxt_host
            target = parsed.url
        raise NetworkPolicyError("too many redirects")
    except BaseException:
        if resp is not None:
            try:
                resp.close()
            except Exception:  # noqa: BLE001, S110 — cleanup never raises
                pass
        raise
