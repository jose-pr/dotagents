"""certifi shim: where() resolves a CA bundle from the OS trust store.

No network. Uses tmp files to stand in for OS cert locations.
"""
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
    assert certifi.where() is None


def test_contents_reads_resolved_bundle(tmp_path, monkeypatch):
    bundle = tmp_path / "ca.pem"
    bundle.write_bytes(b"PEMDATA")
    monkeypatch.setenv("SSL_CERT_FILE", str(bundle))
    assert certifi.contents() == b"PEMDATA"
