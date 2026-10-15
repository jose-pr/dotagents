"""The pip wheel dotagents runs when an interpreter has no pip of its own.

A venv created where ``ensurepip`` is missing (Debian's split ``python3-venv``,
a minimal container) has no pip. pip runs straight from its wheel
(``python pip.whl/pip --python <venv python> install ...``), so one wheel
serves every venv. It is looked for, in order:

1. the cache, ``<user store>/.cache/pip/``;
2. a wheel vendored into the package (``build-pyz --with-pip``);
3. ``AGENTS_PIP_SOURCE`` and pip's own ``find-links`` (a directory of wheels,
   a ``file:`` / ``http(s):`` URL, or any URI pathapi-uri speaks);
4. the package index: ``PIP_INDEX_URL``, else ``index-url`` from pip's config
   files, else PyPI -- read through the simple API (PEP 691 JSON, else PEP 503
   HTML).

Every candidate must match :data:`PIP_SHA256`, so a mirror or a directory can
only ever supply the exact wheel pinned here. pip 25.3 is the last release
that runs on Python 3.9, dotagents' floor.
"""

from __future__ import annotations

import configparser
import hashlib
import html.parser
import json
import os
import ssl
import sys
from pathlib import Path
from typing import Optional
from urllib import parse, request

PIP_VERSION = "25.3"
PIP_WHEEL = "pip-%s-py3-none-any.whl" % PIP_VERSION
PIP_SHA256 = "9655943313a94722b7774661c21049070f6bbb0a1516bf02f7c8d5d9201514cd"
#: Where a wheel vendored by ``build-pyz --with-pip`` sits inside the package.
VENDORED = ("_vendor", PIP_WHEEL)
#: A directory of wheels, or a URI to one, consulted before the index; also
#: handed to pip as ``--find-links`` for the requirements themselves.
PIP_SOURCE_ENV = "AGENTS_PIP_SOURCE"
DEFAULT_INDEX = "https://pypi.org/simple"
_TIMEOUT = 60


class PipUnavailable(SystemExit):
    """No source produced the pinned pip wheel."""


def pip_config(environ: "Optional[dict]" = None) -> "dict[str, str]":
    """pip's settings that matter for fetching a wheel -- ``index-url``,
    ``find-links``, ``cert``, ``trusted-host``, ``proxy`` -- from ``PIP_*``
    variables over pip's config files (``[install]`` over ``[global]``; the
    first file that sets a key wins, in pip's precedence)."""
    env = os.environ if environ is None else environ
    found: "dict[str, str]" = {}
    parser = configparser.RawConfigParser()
    for path in _config_files(env):
        try:
            parser.read(str(path), encoding="utf-8")
        except (OSError, configparser.Error):
            continue
        for section in ("install", "global"):
            if parser.has_section(section):
                for key, value in parser.items(section):
                    found.setdefault(key, value.strip())
        parser = configparser.RawConfigParser()
    for key in ("index-url", "find-links", "cert", "trusted-host", "proxy"):
        value = env.get("PIP_" + key.upper().replace("-", "_"))
        if value:
            found[key] = value
    return found


def _config_files(env) -> "list[Path]":
    """pip's config files, highest precedence first."""
    if env.get("PIP_CONFIG_FILE"):
        return [Path(env["PIP_CONFIG_FILE"])]
    home = Path(env.get("USERPROFILE") or env.get("HOME") or Path.home())
    if sys.platform == "win32":
        files = [Path(env.get("APPDATA", home / "AppData" / "Roaming")) / "pip" / "pip.ini",
                 home / "pip" / "pip.ini"]
        if env.get("PROGRAMDATA"):
            files.append(Path(env["PROGRAMDATA"]) / "pip" / "pip.ini")
        return files
    xdg = Path(env.get("XDG_CONFIG_HOME") or home / ".config")
    files = [xdg / "pip" / "pip.conf", home / ".pip" / "pip.conf"]
    if sys.platform == "darwin":
        files.append(home / "Library" / "Application Support" / "pip" / "pip.conf")
    return files + [Path("/etc/xdg/pip/pip.conf"), Path("/etc/pip.conf")]


def pip_wheel(cache_dir: Path, *, logger=None, environ: "Optional[dict]" = None) -> Path:
    """The pinned pip wheel as a local file under ``cache_dir``, fetched or
    copied there on first use. Raises :class:`PipUnavailable` naming every
    source tried."""
    env = os.environ if environ is None else environ
    cached = Path(cache_dir) / PIP_WHEEL
    if _matches(cached):
        return cached
    tried = ["the cache (%s)" % cached]
    data = _vendored()
    if data is not None:
        return _store(cached, data, "the vendored copy", logger)
    tried.append("a vendored copy (none: build-pyz --with-pip adds one)")
    config = pip_config(env)
    for source in _find_links(env, config):
        tried.append(source)
        try:
            data = _from_find_links(source, config)
        except (OSError, ValueError) as exc:
            _log(logger, "warning", "pip wheel: %s: %s", _redact(source), exc)
            continue
        if data is not None:
            return _store(cached, data, _redact(source), logger)
    index = config.get("index-url") or DEFAULT_INDEX
    tried.append(index)
    try:
        data = _from_index(index, config)
    except (OSError, ValueError) as exc:
        _log(logger, "warning", "pip wheel: %s: %s", _redact(index), exc)
        data = None
    if data is not None:
        return _store(cached, data, _redact(index), logger)
    raise PipUnavailable(
        "error: no pip available: the interpreter has no ensurepip and %s was not "
        "found in %s. Set %s to a directory of wheels, configure a reachable index "
        "(PIP_INDEX_URL or pip.ini/pip.conf), or use a dotagents.pyz built with "
        "--with-pip" % (PIP_WHEEL, "; ".join(_redact(t) for t in tried), PIP_SOURCE_ENV)
    )


def find_links(environ: "Optional[dict]" = None, *, cache_dir: Path, logger=None) -> "list[str]":
    """What to hand pip as ``--find-links`` for requirements: ``AGENTS_PIP_SOURCE``,
    a URI pip cannot read itself (``s3:``, ``sftp:``, ...) copied to a local
    directory under ``cache_dir`` first. Empty when it is unset."""
    env = os.environ if environ is None else environ
    source = env.get(PIP_SOURCE_ENV)
    if not source:
        return []
    if _pip_reads(source):
        return [source]
    return [str(_materialize(source, Path(cache_dir) / "source", logger))]


def _find_links(env, config) -> "list[str]":
    out = []
    for value in (env.get(PIP_SOURCE_ENV), config.get("find-links")):
        out += [v for v in (value or "").split() if v and v not in out]
    return out


def _pip_reads(source: str) -> bool:
    scheme = parse.urlsplit(source).scheme.lower()
    return scheme in ("", "file", "http", "https") or (len(scheme) == 1 and sys.platform == "win32")


def _local(source: str) -> "Optional[Path]":
    split = parse.urlsplit(source)
    if split.scheme.lower() == "file":
        return Path(request.url2pathname(split.path))
    if not split.scheme or (len(split.scheme) == 1 and sys.platform == "win32"):
        return Path(source)
    return None


def _from_find_links(source: str, config) -> "Optional[bytes]":
    local = _local(source)
    if local is not None:
        candidate = local / PIP_WHEEL if local.is_dir() else local
        return candidate.read_bytes() if candidate.name == PIP_WHEEL and candidate.is_file() else None
    if parse.urlsplit(source).scheme.lower() in ("http", "https"):
        return _from_listing(source, config, json_api=False)
    path = _uri(source) / PIP_WHEEL
    return path.read_bytes() if path.is_file() else None


def _from_index(index: str, config) -> "Optional[bytes]":
    return _from_listing(index.rstrip("/") + "/pip/", config, json_api=True)


def _from_listing(url: str, config, *, json_api: bool) -> "Optional[bytes]":
    accept = "application/vnd.pypi.simple.v1+json, text/html;q=0.1" if json_api else "text/html"
    body, ctype, final = _get(url, config, accept)
    links: "dict[str, str]" = {}
    if "json" in ctype:
        for entry in json.loads(body.decode("utf-8")).get("files", []):
            links[entry.get("filename", "")] = entry.get("url", "")
    else:
        anchors = _Anchors()
        anchors.feed(body.decode("utf-8", "replace"))
        for href, text in anchors.links:
            name = text.strip() or parse.unquote(parse.urlsplit(href).path.rsplit("/", 1)[-1])
            links[name] = href
    href = links.get(PIP_WHEEL)
    if not href:
        return None
    data, _ctype, _final = _get(parse.urljoin(final, href), config, "*/*")
    return data


class _Anchors(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: "list[tuple[str, str]]" = []
        self._href: "Optional[str]" = None
        self._text: "list[str]" = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self._href, self._text = dict(attrs).get("href"), []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self._href is not None:
            self.links.append((self._href, "".join(self._text)))
            self._href = None


def _get(url: str, config, accept: str) -> "tuple[bytes, str, str]":
    """GET ``url`` honouring pip's ``cert``, ``trusted-host`` and ``proxy`` and
    credentials in the URL; ``(body, content type, final url)``."""
    split = parse.urlsplit(url)
    headers = {"Accept": accept, "User-Agent": "dotagents"}
    if split.username or split.password:
        import base64

        token = "%s:%s" % (parse.unquote(split.username or ""), parse.unquote(split.password or ""))
        headers["Authorization"] = "Basic " + base64.b64encode(token.encode("utf-8")).decode("ascii")
        netloc = split.hostname + (":%d" % split.port if split.port else "")
        url = parse.urlunsplit(split._replace(netloc=netloc))
    handlers: "list" = []
    if split.scheme == "https":
        trusted = (config.get("trusted-host") or "").split()
        if split.hostname in trusted:
            context = ssl._create_unverified_context()
        else:
            context = ssl.create_default_context(cafile=config.get("cert") or None)
        handlers.append(request.HTTPSHandler(context=context))
    if config.get("proxy"):
        handlers.append(request.ProxyHandler({"http": config["proxy"], "https": config["proxy"]}))
    opener = request.build_opener(*handlers)
    with opener.open(request.Request(url, headers=headers), timeout=_TIMEOUT) as response:
        return response.read(), response.headers.get("Content-Type", ""), response.geturl()


def _uri(source: str):
    from dotagents._sources import uri_path_class

    cls = uri_path_class()
    if cls is None:
        raise ValueError("reading %s needs the uri extra (pip install 'dotagents-cli[uri]')"
                         % parse.urlsplit(source).scheme)
    return cls(source)


def _materialize(source: str, root: Path, logger) -> Path:
    """Copy the distributions under URI ``source`` into a local directory."""
    dest = root / hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    dest.mkdir(parents=True, exist_ok=True)
    for entry in _uri(source).iterdir():
        name = entry.name
        if name.endswith((".whl", ".tar.gz", ".zip")) and not (dest / name).is_file():
            _write(dest / name, entry.read_bytes())
    _log(logger, "info", "pip source %s copied to %s", _redact(source), dest)
    return dest


def _vendored() -> "Optional[bytes]":
    try:
        from importlib import resources

        resource = resources.files("dotagents").joinpath(*VENDORED)
        return resource.read_bytes() if resource.is_file() else None
    except (AttributeError, OSError, ModuleNotFoundError, TypeError):
        return None


def _matches(path: Path) -> bool:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest() == PIP_SHA256
    except OSError:
        return False


def _store(dest: Path, data: bytes, origin: str, logger) -> Path:
    digest = hashlib.sha256(data).hexdigest()
    if digest != PIP_SHA256:
        raise PipUnavailable("error: %s from %s has sha256 %s, expected %s"
                             % (PIP_WHEEL, origin, digest, PIP_SHA256))
    _write(dest, data)
    _log(logger, "info", "pip wheel: %s from %s", PIP_WHEEL, origin)
    return dest


def _write(dest: Path, data: bytes) -> None:
    """Write through a sibling temp file, so a reader never sees half a wheel."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".part")
    tmp.write_bytes(data)
    os.replace(str(tmp), str(dest))


def _redact(text: str) -> str:
    from dotagents._sources import redact

    return redact(text)


def _log(logger, level: str, message: str, *args) -> None:
    if logger is not None:
        getattr(logger, level)(message, *args)


__all__ = ["PIP_SHA256", "PIP_SOURCE_ENV", "PIP_VERSION", "PIP_WHEEL", "PipUnavailable",
           "find_links", "pip_config", "pip_wheel"]
