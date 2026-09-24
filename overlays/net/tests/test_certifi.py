"""certifi shim: where() names a CA bundle FILE from the OS trust store -- an
override variable, the system's bundle file, or one built from the system's
certificates (a one-minute cache) -- and never a packaged certifi bundle.

No network. tmp files stand in for OS cert locations.
"""
import os
import ssl
import sys
import time

import pytest

import certifi  # noqa: E402  (the overlay's shim, via conftest lib path)


class _NoPaths:
    cafile = None
    capath = None
    openssl_capath = None


def _no_system_file(monkeypatch):
    for var in certifi.OVERRIDE_VARS + ("SSL_CERT_DIR",):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _NoPaths())
    monkeypatch.setattr(certifi, "COMMON_PATHS", [])


def _bundle_home(monkeypatch, tmp_path):
    """Point the built bundle at tmp (Windows: %TEMP%; POSIX: XDG_RUNTIME_DIR)."""
    monkeypatch.setattr(certifi.tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path / "dotagents-net" / "cacert.pem"


def _only_dir_certs(monkeypatch, tmp_path):
    certs = tmp_path / "certs"
    certs.mkdir()
    (certs / "ab12cd34.0").write_text(PEM, encoding="ascii")
    (certs / "README").write_text("not a certificate\n", encoding="ascii")
    monkeypatch.setenv("SSL_CERT_DIR", str(certs))
    monkeypatch.setattr(certifi, "COMMON_DIRS", [])
    monkeypatch.setattr(certifi, "_windows_certs", lambda: [])
    monkeypatch.setattr(certifi, "_default_context_certs", lambda: [])
    return certs


@pytest.mark.parametrize("var", ["SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE"])
def test_an_override_variable_wins(tmp_path, monkeypatch, var):
    for other in certifi.OVERRIDE_VARS:
        monkeypatch.delenv(other, raising=False)
    bundle = tmp_path / "custom-ca.pem"
    bundle.write_text(PEM, encoding="ascii")
    monkeypatch.setenv(var, str(bundle))
    assert certifi.where() == str(bundle)


def test_an_override_naming_nothing_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist.pem"))
    assert certifi.where() != str(tmp_path / "does-not-exist.pem")


def test_the_systems_default_bundle_file(tmp_path, monkeypatch):
    _no_system_file(monkeypatch)
    cafile = tmp_path / "default-ca.pem"
    cafile.write_text(PEM, encoding="ascii")

    class _Paths(_NoPaths):
        pass

    _Paths.cafile = str(cafile)
    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _Paths())
    assert certifi.where() == str(cafile)


def test_a_well_known_os_bundle_file(tmp_path, monkeypatch):
    _no_system_file(monkeypatch)
    common = tmp_path / "ca-certificates.crt"
    common.write_text(PEM, encoding="ascii")
    monkeypatch.setattr(certifi, "COMMON_PATHS", [str(common)])
    assert certifi.where() == str(common)


def test_a_certs_directory_without_a_bundle_file_is_built_into_one(tmp_path, monkeypatch):
    """A hashed certs dir and no bundle file: some libraries take only a file."""
    _no_system_file(monkeypatch)
    _only_dir_certs(monkeypatch, tmp_path)
    target = _bundle_home(monkeypatch, tmp_path)
    assert certifi.where() == str(target)
    assert target.read_text(encoding="ascii").count("BEGIN CERTIFICATE") == 1
    ssl.create_default_context(cafile=str(target))  # a bundle OpenSSL accepts


def test_the_built_bundle_is_a_one_minute_cache(tmp_path, monkeypatch):
    _no_system_file(monkeypatch)
    _only_dir_certs(monkeypatch, tmp_path)
    target = _bundle_home(monkeypatch, tmp_path)
    assert certifi.where() == str(target)
    built = []
    real_build = certifi.build_bundle
    monkeypatch.setattr(certifi, "build_bundle", lambda path=None: built.append(path) or real_build(path))
    assert certifi.where() == str(target) and built == [], "fresh: reused"
    old = time.time() - certifi.BUILT_BUNDLE_MAX_AGE - 1
    os.utime(target, (old, old))
    assert certifi.where() == str(target) and built == [str(target)], "older than a minute: rebuilt"
    assert certifi.BUILT_BUNDLE_MAX_AGE == 60


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows certificate stores")
def test_windows_builds_its_bundle_from_the_system_stores(tmp_path, monkeypatch):
    """Windows keeps its roots in the certificate store, not a file: where()
    returned None and every `requests` HTTPS call failed."""
    _no_system_file(monkeypatch)
    target = _bundle_home(monkeypatch, tmp_path)
    assert certifi.where() == str(target)
    ctx = ssl.create_default_context(cafile=str(target))
    assert ctx.cert_store_stats()["x509_ca"] > 10


def test_windows_store_entries_are_tls_server_certificates_only(monkeypatch):
    der = ssl.PEM_cert_to_DER_cert(PEM)
    entries = {
        "ROOT": [(der, "x509_asn", True), (b"crl", "pkcs_7_asn", True)],
        "CA": [(b"email", "x509_asn", {"1.3.6.1.5.5.7.3.4"})],
    }
    monkeypatch.setattr(certifi.ssl, "enum_certificates", lambda store: entries[store], raising=False)
    assert certifi._windows_certs() == [der]


def test_never_a_packaged_certifi_bundle(tmp_path, monkeypatch, capsys):
    """No system certificates at all: None and a note naming the variables --
    even with a real certifi installed right there."""
    _no_system_file(monkeypatch)
    monkeypatch.setattr(certifi, "system_certs", lambda: [])
    _bundle_home(monkeypatch, tmp_path)
    real = tmp_path / "site" / "certifi"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text("", encoding="utf-8")
    (real / "cacert.pem").write_text(PEM, encoding="ascii")
    monkeypatch.setattr(certifi.sys, "path", [str(tmp_path / "site")] + sys.path)
    monkeypatch.setattr(certifi, "_warned", False)
    assert certifi.where() is None
    err = capsys.readouterr().err
    assert "SSL_CERT_FILE" in err and "REQUESTS_CA_BUNDLE" in err
    certifi.where()
    assert capsys.readouterr().err == "", "said once"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_the_built_bundle_lives_in_a_private_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(certifi, "system_certs", lambda: [ssl.PEM_cert_to_DER_cert(PEM)])
    target = _bundle_home(monkeypatch, tmp_path)
    assert certifi.build_bundle() == str(target)
    assert os.stat(target.parent).st_mode & 0o077 == 0


def test_contents_reads_resolved_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(b"PEMDATA")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert certifi.contents() == b"PEMDATA"


# A self-signed CA certificate (test data only; no private key anywhere).
PEM = """-----BEGIN CERTIFICATE-----
MIIBjTCCATOgAwIBAgIUCvo8h5JnkB38EC2xUUkCjlyUVTwwCgYIKoZIzj0EAwIw
HDEaMBgGA1UEAwwRZG90YWdlbnRzIHRlc3QgQ0EwHhcNMjYwOTI0MTUxMjI2WhcN
MzYwOTIxMTUxMjI2WjAcMRowGAYDVQQDDBFkb3RhZ2VudHMgdGVzdCBDQTBZMBMG
ByqGSM49AgEGCCqGSM49AwEHA0IABCLCCoqURFoXY3/IDyWarI11noSKyROHmy4H
yf0WM8IvyUwR4U+6E2MFPhwm2RnW2F+ymIyghLv7f9szwNS4A2KjUzBRMB0GA1Ud
DgQWBBTS1Ldkh6RZTMf2ctC3TWh33WUGTTAfBgNVHSMEGDAWgBTS1Ldkh6RZTMf2
ctC3TWh33WUGTTAPBgNVHRMBAf8EBTADAQH/MAoGCCqGSM49BAMCA0gAMEUCIBI7
77eWb6VkIDjuCWlNFH938aTbJEq0O48bdgo4iC88AiEA/xcYnHP6s5n4HZ4tkqyj
alYML1xKoCI+heBNx7hU/Zs=
-----END CERTIFICATE-----
"""
