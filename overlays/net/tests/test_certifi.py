"""certifi shim: where() resolves a CA bundle from the OS trust store.

No network. Uses tmp files to stand in for OS cert locations.
"""
import ssl
import sys

import pytest

import certifi  # noqa: E402  (the overlay's shim, via conftest lib path)


def test_ssl_cert_file_env_wins(tmp_path, monkeypatch):
    bundle = tmp_path / "custom-ca.pem"
    bundle.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert certifi.where() == str(bundle)


def test_ssl_cert_file_ignored_when_missing(tmp_path, monkeypatch):
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "does-not-exist.pem"))
    # Falls through to ssl.get_default_verify_paths()/common paths -> some path
    # or None, but must NOT return the nonexistent env value.
    result = certifi.where()
    assert result != str(tmp_path / "does-not-exist.pem")


def test_falls_back_to_ssl_default(tmp_path, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    fake_cafile = tmp_path / "default-ca.pem"
    fake_cafile.write_text("x", encoding="utf-8")

    class _Paths:
        cafile = str(fake_cafile)
        capath = None

    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _Paths())
    assert certifi.where() == str(fake_cafile)


def test_falls_back_to_common_path(tmp_path, monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class _Paths:
        cafile = None
        capath = None

    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _Paths())

    common = tmp_path / "ca-certificates.crt"
    common.write_text("x", encoding="utf-8")
    monkeypatch.setattr(certifi, "COMMON_PATHS", [str(common)])
    assert certifi.where() == str(common)


def test_none_when_nothing_found(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class _Paths:
        cafile = None
        capath = None

    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _Paths())
    monkeypatch.setattr(certifi, "COMMON_PATHS", ["/no/such/path/ca.crt"])
    monkeypatch.setattr(certifi.sys, "platform", "linux")
    monkeypatch.setattr(certifi, "_real_certifi_bundle", lambda: None)
    assert certifi.where() is None


def test_contents_reads_resolved_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(b"PEMDATA")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert certifi.contents() == b"PEMDATA"


def _no_os_bundle(monkeypatch):
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)

    class _Paths:
        cafile = None
        capath = None

    monkeypatch.setattr(certifi.ssl, "get_default_verify_paths", lambda: _Paths())
    monkeypatch.setattr(certifi, "COMMON_PATHS", [])


@pytest.mark.skipif(sys.platform != "win32", reason="the Windows certificate stores")
def test_windows_exports_the_system_stores_to_a_usable_bundle(tmp_path, monkeypatch):
    """Windows has no CA file for OpenSSL, so where() returned None and every
    `requests` HTTPS call failed once this shim was on PYTHONPATH."""
    _no_os_bundle(monkeypatch)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    path = certifi.where()
    assert path == str(tmp_path / "dotagents" / "net" / "cacert.pem")
    ctx = ssl.create_default_context(cafile=path)
    assert ctx.cert_store_stats()["x509_ca"] > 10
    # Reused while fresh; no second export.
    monkeypatch.setattr(certifi, "_export_windows_stores", lambda p: pytest.fail("re-exported"))
    assert certifi.where() == path


def test_the_export_keeps_only_tls_server_certificates(tmp_path, monkeypatch):
    der = ssl.PEM_cert_to_DER_cert(PEM)
    entries = {
        "ROOT": [(der, "x509_asn", True), (b"crl", "pkcs_7_asn", True)],
        "CA": [(der, "x509_asn", {"1.3.6.1.5.5.7.3.4"})],  # email only, and a duplicate
    }
    monkeypatch.setattr(certifi.ssl, "enum_certificates", lambda store: entries[store], raising=False)
    out = certifi._export_windows_stores(str(tmp_path / "b" / "cacert.pem"))
    text = open(out, encoding="ascii").read()
    assert text.count("BEGIN CERTIFICATE") == 1
    ssl.create_default_context(cafile=out)  # parses


def test_falls_back_to_an_installed_certifi(tmp_path, monkeypatch):
    _no_os_bundle(monkeypatch)
    monkeypatch.setattr(certifi.sys, "platform", "linux")
    real = tmp_path / "site" / "certifi"
    real.mkdir(parents=True)
    (real / "__init__.py").write_text("", encoding="utf-8")
    (real / "cacert.pem").write_text(PEM, encoding="ascii")
    monkeypatch.setattr(certifi.sys, "path", [str(tmp_path / "site")] + sys.path)
    assert certifi.where() == str(real / "cacert.pem")


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
