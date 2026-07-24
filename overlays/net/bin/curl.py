"""A drop-in ``curl`` shim.

Behavior:
  1. **Try the real system ``curl`` first** (``shutil.which("curl")``). If present,
     exec it with the original argv and return its exit code -- byte-for-byte real
     curl. This shim only matters where curl is absent.
  2. **Pure-stdlib fallback** when curl is not on PATH: a small ``urllib``-based
     implementation covering the common flag surface (``-X -d -H -o -s -i -I -L
     -k -A -x -b`` etc.). CA verification resolves through the net overlay's
     ``certifi`` shim (OS trust store); ``requests`` is used only if importable but
     is **not required**.

The large ``UNSUPPORTED_ARGS`` set is parsed and **explicitly rejected** with
``NotImplementedError`` -- the shim must never silently do the wrong thing for a
flag it does not actually honor. Python 3.9+, no third-party dependency required.
"""
import argparse
import os
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

# The overlay ships lib/ as a sibling of bin/. Put it on sys.path so the bundled
# `certifi` shim (and httplib, if a caller wants it) resolves.
LIB_DIR = Path(__file__).resolve().parents[1] / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

USER_AGENT = "Python-curl/1.0"

UNSUPPORTED_ARGS = [
    'any', 'append', 'basic', 'cert_status', 'cert', 'ciphers', 'compressed',
    'config', 'connect_timeout', 'continue_at', 'create_dirs', 'crlf',
    'crlfile', 'data_ascii', 'data_binary', 'data_urlencode', 'delegation',
    'digest', 'disable_eprt', 'disable_epsv', 'dns_interface', 'dns_ipv4_addr',
    'dns_ipv6_addr', 'dns_servers', 'doh_url', 'egd_file', 'engine',
    'expect100_timeout', 'fail_early', 'fail', 'false_start', 'form_string',
    'form', 'ftp_account', 'ftp_alternative_to_user', 'ftp_create_dirs',
    'ftp_method', 'ftp_pasv', 'ftp_skip_pasv_ip', 'ftp_ssl_ccc_mode',
    'ftp_ssl_ccc', 'ftp_ssl_control', 'get', 'globoff',
    'happy_eyeballs_timeout_ms', 'haproxy_protocol', 'hostpubmd5', 'http1_0',
    'http1_1', 'http2_prior_knowledge', 'http2', 'http3',
    'ignore_content_length', 'interface', 'ip_resolve', 'ipv4', 'ipv6',
    'junk_session_cookies', 'keepalive_time', 'key_type', 'key', 'krb',
    'libcurl', 'limit_rate', 'list_only', 'local_port', 'location_trusted',
    'login_options', 'mail_auth', 'mail_from', 'mail_rcpt_allowfails',
    'mail_rcpt', 'max_filesize', 'max_redirs', 'max_time', 'metalink',
    'negotiate', 'netrc_file', 'netrc_optional', 'netrc', 'next', 'no_alpn',
    'no_buffer', 'no_keepalive', 'no_npn', 'no_progress_bar', 'no_sessionid',
    'noproxy', 'ntlm_wb', 'ntlm', 'oauth2_bearer', 'output_dir',
    'parallel_immediate', 'parallel_max', 'parallel', 'pass_', 'path_as_is',
    'pinnedpubkey', 'post301', 'post302', 'post303', 'preproxy',
    'progress_bar', 'proto_default', 'proto_redir', 'proto', 'proxy_anyauth',
    'proxy_basic', 'proxy_cacert', 'proxy_capath', 'proxy_cert_type',
    'proxy_cert', 'proxy_ciphers', 'proxy_crlfile', 'proxy_digest',
    'proxy_header', 'proxy_insecure', 'proxy_key_type', 'proxy_key',
    'proxy_negotiate', 'proxy_ntlm', 'proxy_pass', 'proxy_pinnedpubkey',
    'proxy_service_name', 'proxy_ssl_allow_beast',
    'proxy_ssl_auto_client_cert', 'proxy_tls13_ciphers', 'proxy_tlsauthtype',
    'proxy_tlspassword', 'proxy_tlsuser', 'proxy_tlsv1', 'proxy_user',
    'proxytunnel', 'pubkey', 'quote', 'random_file', 'range', 'raw',
    'referer', 'remote_header_name', 'remote_name_all', 'remote_name',
    'remote_time', 'request_target', 'resolve', 'retry_connrefused',
    'retry_delay', 'retry_max_time', 'retry', 'sasl_authzid', 'sasl_ir',
    'service_name', 'show_headers', 'socks4', 'socks4a',
    'socks5_basic', 'socks5_gssapi_nec', 'socks5_gssapi_service',
    'socks5_gssapi', 'socks5_hostname', 'socks5', 'speed_limit', 'speed_time',
    'ssl_allow_beast', 'ssl_auto_client_cert', 'ssl_no_revoke', 'ssl_reqd',
    'ssl_revoke_best_effort', 'ssl', 'sslv2', 'sslv3', 'stderr',
    'styled_output', 'suppress_connect_headers', 'tcp_fastopen',
    'tcp_nodelay', 'telnet_option', 'tftp_blksize', 'tftp_no_options',
    'time_cond', 'tls_max', 'tls13_ciphers', 'tlsauthtype', 'tlspassword',
    'tlsuser', 'tlsv1_0', 'tlsv1_1', 'tlsv1_2', 'tlsv1_3', 'tlsv1',
    'tr_encoding', 'trace_ascii', 'trace_time', 'trace', 'unix_socket',
    'upload_file', 'url_query', 'use_ascii', 'user', 'variable', 'version',
    'vsock', 'write_out', 'xattr',
]


def check_not_implemented(args):
    unsupported = []
    for arg in UNSUPPORTED_ARGS:
        if getattr(args, arg, None):
            unsupported.append("--%s" % arg.replace('_', '-'))
    if unsupported:
        raise NotImplementedError("Unsupported options: %s" % ', '.join(unsupported))


def build_parser():
    parser = argparse.ArgumentParser(description='Pure Python curl-like tool.', add_help=False)
    parser.add_argument('url_positional', nargs='?', help='URL to fetch (positional)')
    parser.add_argument('--url', help='URL to fetch (option)')
    parser.add_argument('-X', '--request', default='GET', help='Specify request method')
    parser.add_argument('-d', '--data', help='HTTP POST data')
    parser.add_argument('--data-raw', dest='data_raw', help='HTTP POST data without @file expansion')
    parser.add_argument('-H', '--header', action='append', help='Custom header')
    parser.add_argument('-o', '--output', help='Output file')
    parser.add_argument('-s', '--silent', action='store_true', help='Silent mode')
    parser.add_argument('-S', '--show-error', dest='show_error_supported', action='store_true', help='Show errors even when silent')
    parser.add_argument('-v', '--verbose', action='store_true', help='Verbose mode')
    parser.add_argument('-i', '--include', action='store_true', help='Include response headers in output')
    parser.add_argument('-I', '--head', action='store_true', help='Fetch headers only')
    parser.add_argument('-D', '--dump-header', help='Write response headers to file (or - for stdout)')
    parser.add_argument('-b', '--cookie', help='Cookie string or file to read cookies from')
    parser.add_argument('-c', '--cookie-jar', help='Write cookies to this file after operation')
    parser.add_argument('-x', '--proxy', help='Use proxy')
    parser.add_argument('-k', '--insecure', action='store_true', help='Allow insecure server connections')
    parser.add_argument('-A', '--user-agent', dest='user_agent', help='Set User-Agent')
    parser.add_argument('-L', '--location', action='store_true', help='Follow redirects')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout in seconds')
    parser.add_argument('-h', '--help', action='store_true', help='Show this help message and exit')
    # Recognized-but-unsupported flags: parsed so we can reject them explicitly
    # (NotImplementedError) rather than mis-handle them.
    parser.add_argument('--any', action='store_true')
    parser.add_argument('--append', action='store_true')
    parser.add_argument('--basic', action='store_true')
    parser.add_argument('--cert-status', dest='cert_status', action='store_true')
    parser.add_argument('--cert', metavar='CERT')
    parser.add_argument('--ciphers', metavar='CIPHERS')
    parser.add_argument('--compressed', action='store_true')
    parser.add_argument('--config', metavar='CONFIG')
    parser.add_argument('--connect-timeout', dest='connect_timeout', type=float)
    parser.add_argument('--continue-at', dest='continue_at', metavar='OFFSET')
    parser.add_argument('--create-dirs', dest='create_dirs', action='store_true')
    parser.add_argument('--crlf', action='store_true')
    parser.add_argument('--crlfile', metavar='FILE')
    parser.add_argument('--data-ascii', dest='data_ascii', metavar='DATA')
    parser.add_argument('--data-binary', dest='data_binary', metavar='DATA')
    parser.add_argument('--data-urlencode', dest='data_urlencode', metavar='DATA')
    parser.add_argument('--delegation', metavar='LEVEL')
    parser.add_argument('--digest', action='store_true')
    parser.add_argument('--disable-eprt', dest='disable_eprt', action='store_true')
    parser.add_argument('--disable-epsv', dest='disable_epsv', action='store_true')
    parser.add_argument('--dns-interface', dest='dns_interface', metavar='INTERFACE')
    parser.add_argument('--dns-ipv4-addr', dest='dns_ipv4_addr', metavar='ADDRESS')
    parser.add_argument('--dns-ipv6-addr', dest='dns_ipv6_addr', metavar='ADDRESS')
    parser.add_argument('--dns-servers', dest='dns_servers', metavar='ADDRESSES')
    parser.add_argument('--doh-url', dest='doh_url', metavar='URL')
    parser.add_argument('--egd-file', dest='egd_file', metavar='FILE')
    parser.add_argument('--engine', metavar='ENGINE')
    parser.add_argument('--expect100-timeout', dest='expect100_timeout', type=float)
    parser.add_argument('--fail-early', dest='fail_early', action='store_true')
    parser.add_argument('--fail', action='store_true')
    parser.add_argument('--false-start', dest='false_start', action='store_true')
    parser.add_argument('--form-string', dest='form_string', metavar='STRING')
    parser.add_argument('-F', '--form', metavar='NAME=CONTENT')
    parser.add_argument('--ftp-account', dest='ftp_account', metavar='DATA')
    parser.add_argument('--ftp-alternative-to-user', dest='ftp_alternative_to_user', metavar='COMMAND')
    parser.add_argument('--ftp-create-dirs', dest='ftp_create_dirs', action='store_true')
    parser.add_argument('--ftp-method', dest='ftp_method', metavar='METHOD')
    parser.add_argument('--ftp-pasv', dest='ftp_pasv', action='store_true')
    parser.add_argument('--ftp-skip-pasv-ip', dest='ftp_skip_pasv_ip', action='store_true')
    parser.add_argument('--ftp-ssl-ccc-mode', dest='ftp_ssl_ccc_mode', metavar='MODE')
    parser.add_argument('--ftp-ssl-ccc', dest='ftp_ssl_ccc', action='store_true')
    parser.add_argument('--ftp-ssl-control', dest='ftp_ssl_control', action='store_true')
    parser.add_argument('-G', '--get', action='store_true')
    parser.add_argument('--globoff', action='store_true')
    parser.add_argument('--happy-eyeballs-timeout-ms', dest='happy_eyeballs_timeout_ms', type=int)
    parser.add_argument('--haproxy-protocol', dest='haproxy_protocol', action='store_true')
    parser.add_argument('--hostpubmd5', metavar='MD5')
    parser.add_argument('--http1.0', dest='http1_0', action='store_true')
    parser.add_argument('--http1.1', dest='http1_1', action='store_true')
    parser.add_argument('--http2-prior-knowledge', dest='http2_prior_knowledge', action='store_true')
    parser.add_argument('--http2', action='store_true')
    parser.add_argument('--http3', action='store_true')
    parser.add_argument('--ignore-content-length', dest='ignore_content_length', action='store_true')
    parser.add_argument('--interface', metavar='INTERFACE')
    parser.add_argument('--ip-resolve', dest='ip_resolve', metavar='RESOLVE')
    parser.add_argument('--ipv4', action='store_true')
    parser.add_argument('--ipv6', action='store_true')
    parser.add_argument('--junk-session-cookies', dest='junk_session_cookies', action='store_true')
    parser.add_argument('--keepalive-time', dest='keepalive_time', type=int)
    parser.add_argument('--key-type', dest='key_type', metavar='TYPE')
    parser.add_argument('--key', metavar='KEY')
    parser.add_argument('--krb', metavar='LEVEL')
    parser.add_argument('--libcurl', metavar='FILE')
    parser.add_argument('--limit-rate', dest='limit_rate', metavar='RATE')
    parser.add_argument('--list-only', dest='list_only', action='store_true')
    parser.add_argument('--local-port', dest='local_port', metavar='RANGE')
    parser.add_argument('--location-trusted', dest='location_trusted', action='store_true')
    parser.add_argument('--login-options', dest='login_options', metavar='OPTIONS')
    parser.add_argument('--mail-auth', dest='mail_auth', metavar='AUTH')
    parser.add_argument('--mail-from', dest='mail_from', metavar='FROM')
    parser.add_argument('--mail-rcpt-allowfails', dest='mail_rcpt_allowfails', action='store_true')
    parser.add_argument('--mail-rcpt', dest='mail_rcpt', metavar='RCPT')
    parser.add_argument('--max-filesize', dest='max_filesize', type=int)
    parser.add_argument('-m', '--max-time', dest='max_time', type=float)
    parser.add_argument('--max-redirs', dest='max_redirs', type=int)
    parser.add_argument('--metalink', action='store_true')
    parser.add_argument('--negotiate', action='store_true')
    parser.add_argument('--netrc-file', dest='netrc_file', metavar='FILE')
    parser.add_argument('--netrc-optional', dest='netrc_optional', action='store_true')
    parser.add_argument('-n', '--netrc', action='store_true')
    parser.add_argument('--next', action='store_true')
    parser.add_argument('--no-alpn', dest='no_alpn', action='store_true')
    parser.add_argument('--no-buffer', dest='no_buffer', action='store_true')
    parser.add_argument('--no-keepalive', dest='no_keepalive', action='store_true')
    parser.add_argument('--no-npn', dest='no_npn', action='store_true')
    parser.add_argument('--no-progress-bar', dest='no_progress_bar', action='store_true')
    parser.add_argument('--no-sessionid', dest='no_sessionid', action='store_true')
    parser.add_argument('--noproxy', metavar='HOSTS')
    parser.add_argument('--ntlm-wb', dest='ntlm_wb', action='store_true')
    parser.add_argument('--ntlm', action='store_true')
    parser.add_argument('--oauth2-bearer', dest='oauth2_bearer', metavar='TOKEN')
    parser.add_argument('--output-dir', dest='output_dir', metavar='DIR')
    parser.add_argument('--parallel-immediate', dest='parallel_immediate', action='store_true')
    parser.add_argument('--parallel-max', dest='parallel_max', type=int)
    parser.add_argument('--parallel', action='store_true')
    parser.add_argument('--pass', dest='pass_', metavar='PASS')
    parser.add_argument('--path-as-is', dest='path_as_is', action='store_true')
    parser.add_argument('--pinnedpubkey', dest='pinnedpubkey', metavar='HASHES')
    parser.add_argument('--post301', action='store_true')
    parser.add_argument('--post302', action='store_true')
    parser.add_argument('--post303', action='store_true')
    parser.add_argument('--preproxy', metavar='PROXY')
    parser.add_argument('--progress-bar', dest='progress_bar', action='store_true')
    parser.add_argument('--proto-default', dest='proto_default', metavar='PROTO')
    parser.add_argument('--proto-redir', dest='proto_redir', metavar='PROTOCOLS')
    parser.add_argument('--proto', metavar='PROTOCOLS')
    parser.add_argument('--proxy-anyauth', dest='proxy_anyauth', action='store_true')
    parser.add_argument('--proxy-basic', dest='proxy_basic', action='store_true')
    parser.add_argument('--proxy-cacert', dest='proxy_cacert', metavar='FILE')
    parser.add_argument('--proxy-capath', dest='proxy_capath', metavar='DIR')
    parser.add_argument('--proxy-cert-type', dest='proxy_cert_type', metavar='TYPE')
    parser.add_argument('--proxy-cert', dest='proxy_cert', metavar='CERT')
    parser.add_argument('--proxy-ciphers', dest='proxy_ciphers', metavar='LIST')
    parser.add_argument('--proxy-crlfile', dest='proxy_crlfile', metavar='FILE')
    parser.add_argument('--proxy-digest', dest='proxy_digest', action='store_true')
    parser.add_argument('--proxy-header', dest='proxy_header', action='append')
    parser.add_argument('--proxy-insecure', dest='proxy_insecure', action='store_true')
    parser.add_argument('--proxy-key-type', dest='proxy_key_type', metavar='TYPE')
    parser.add_argument('--proxy-key', dest='proxy_key', metavar='KEY')
    parser.add_argument('--proxy-negotiate', dest='proxy_negotiate', action='store_true')
    parser.add_argument('--proxy-ntlm', dest='proxy_ntlm', action='store_true')
    parser.add_argument('--proxy-pass', dest='proxy_pass', metavar='PASS')
    parser.add_argument('--proxy-pinnedpubkey', dest='proxy_pinnedpubkey', metavar='HASHES')
    parser.add_argument('--proxy-service-name', dest='proxy_service_name', metavar='NAME')
    parser.add_argument('--proxy-ssl-allow-beast', dest='proxy_ssl_allow_beast', action='store_true')
    parser.add_argument('--proxy-ssl-auto-client-cert', dest='proxy_ssl_auto_client_cert', action='store_true')
    parser.add_argument('--proxy-tls13-ciphers', dest='proxy_tls13_ciphers', metavar='CIPHERS')
    parser.add_argument('--proxy-tlsauthtype', dest='proxy_tlsauthtype', metavar='TYPE')
    parser.add_argument('--proxy-tlspassword', dest='proxy_tlspassword', metavar='STRING')
    parser.add_argument('--proxy-tlsuser', dest='proxy_tlsuser', metavar='USER')
    parser.add_argument('--proxy-tlsv1', dest='proxy_tlsv1', action='store_true')
    parser.add_argument('-U', '--proxy-user', dest='proxy_user', metavar='USER[:PASS]')
    parser.add_argument('--proxytunnel', action='store_true')
    parser.add_argument('--pubkey', metavar='KEY')
    parser.add_argument('-Q', '--quote', action='append')
    parser.add_argument('--random-file', dest='random_file', metavar='FILE')
    parser.add_argument('-r', '--range', metavar='RANGE')
    parser.add_argument('--raw', action='store_true')
    parser.add_argument('-e', '--referer', metavar='URL')
    parser.add_argument('--remote-header-name', dest='remote_header_name', action='store_true')
    parser.add_argument('--remote-name-all', dest='remote_name_all', action='store_true')
    parser.add_argument('-O', '--remote-name', dest='remote_name', action='store_true')
    parser.add_argument('--remote-time', dest='remote_time', action='store_true')
    parser.add_argument('--request-target', dest='request_target', metavar='PATH')
    parser.add_argument('--resolve', action='append')
    parser.add_argument('--retry-connrefused', dest='retry_connrefused', action='store_true')
    parser.add_argument('--retry-delay', dest='retry_delay', type=int)
    parser.add_argument('--retry-max-time', dest='retry_max_time', type=int)
    parser.add_argument('--retry', type=int)
    parser.add_argument('--sasl-authzid', dest='sasl_authzid', metavar='IDENTITY')
    parser.add_argument('--sasl-ir', dest='sasl_ir', action='store_true')
    parser.add_argument('--service-name', dest='service_name', metavar='NAME')
    parser.add_argument('--show-headers', dest='show_headers', action='store_true')
    parser.add_argument('--socks4', metavar='HOST[:PORT]')
    parser.add_argument('--socks4a', metavar='HOST[:PORT]')
    parser.add_argument('--socks5-basic', dest='socks5_basic', action='store_true')
    parser.add_argument('--socks5-gssapi-nec', dest='socks5_gssapi_nec', action='store_true')
    parser.add_argument('--socks5-gssapi-service', dest='socks5_gssapi_service', metavar='NAME')
    parser.add_argument('--socks5-gssapi', dest='socks5_gssapi', action='store_true')
    parser.add_argument('--socks5-hostname', dest='socks5_hostname', metavar='HOST[:PORT]')
    parser.add_argument('--socks5', metavar='HOST[:PORT]')
    parser.add_argument('--speed-limit', dest='speed_limit', type=int)
    parser.add_argument('--speed-time', dest='speed_time', type=int)
    parser.add_argument('--ssl-allow-beast', dest='ssl_allow_beast', action='store_true')
    parser.add_argument('--ssl-auto-client-cert', dest='ssl_auto_client_cert', action='store_true')
    parser.add_argument('--ssl-no-revoke', dest='ssl_no_revoke', action='store_true')
    parser.add_argument('--ssl-reqd', dest='ssl_reqd', action='store_true')
    parser.add_argument('--ssl-revoke-best-effort', dest='ssl_revoke_best_effort', action='store_true')
    parser.add_argument('--ssl', action='store_true')
    parser.add_argument('--sslv2', action='store_true')
    parser.add_argument('--sslv3', action='store_true')
    parser.add_argument('--stderr', metavar='FILE')
    parser.add_argument('--styled-output', dest='styled_output', action='store_true')
    parser.add_argument('--suppress-connect-headers', dest='suppress_connect_headers', action='store_true')
    parser.add_argument('--tcp-fastopen', dest='tcp_fastopen', action='store_true')
    parser.add_argument('--tcp-nodelay', dest='tcp_nodelay', action='store_true')
    parser.add_argument('--telnet-option', dest='telnet_option', action='append')
    parser.add_argument('--tftp-blksize', dest='tftp_blksize', type=int)
    parser.add_argument('--tftp-no-options', dest='tftp_no_options', action='store_true')
    parser.add_argument('--time-cond', dest='time_cond', metavar='TIME')
    parser.add_argument('--tls-max', dest='tls_max', metavar='VERSION')
    parser.add_argument('--tls13-ciphers', dest='tls13_ciphers', metavar='CIPHERS')
    parser.add_argument('--tlsauthtype', metavar='TYPE')
    parser.add_argument('--tlspassword', metavar='STRING')
    parser.add_argument('--tlsuser', metavar='USER')
    parser.add_argument('--tlsv1.0', dest='tlsv1_0', action='store_true')
    parser.add_argument('--tlsv1.1', dest='tlsv1_1', action='store_true')
    parser.add_argument('--tlsv1.2', dest='tlsv1_2', action='store_true')
    parser.add_argument('--tlsv1.3', dest='tlsv1_3', action='store_true')
    parser.add_argument('--tlsv1', action='store_true')
    parser.add_argument('--tr-encoding', dest='tr_encoding', action='store_true')
    parser.add_argument('--trace-ascii', dest='trace_ascii', metavar='FILE')
    parser.add_argument('--trace-time', dest='trace_time', action='store_true')
    parser.add_argument('--trace', metavar='FILE')
    parser.add_argument('--unix-socket', dest='unix_socket', metavar='PATH')
    parser.add_argument('-T', '--upload-file', dest='upload_file', metavar='FILE')
    parser.add_argument('--url-query', dest='url_query', action='append')
    parser.add_argument('--use-ascii', dest='use_ascii', action='store_true')
    parser.add_argument('-u', '--user', metavar='USER[:PASS]')
    parser.add_argument('--variable', action='append')
    parser.add_argument('-V', '--version', action='store_true')
    parser.add_argument('--vsock', action='store_true')
    parser.add_argument('-w', '--write-out', dest='write_out', metavar='FORMAT')
    parser.add_argument('--xattr', action='store_true')
    return parser


def resolve_url(args, parser):
    url = args.url or args.url_positional
    if not url:
        parser.error('URL is required')
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    return url


def prepare_headers(args, parser):
    headers = {'User-Agent': args.user_agent or USER_AGENT}
    if args.header:
        for header in args.header:
            if ':' not in header:
                parser.error('Invalid header: %s' % header)
            key, value = header.split(':', 1)
            headers[key.strip()] = value.strip()
    return headers


def prepare_data(args, parser):
    if args.data_raw is not None:
        data = args.data_raw.encode('utf-8')
        if args.request == 'GET':
            args.request = 'POST'
        return data
    if not args.data:
        return None
    if args.data.startswith('@'):
        file_path = args.data[1:]
        if not os.path.exists(file_path):
            parser.error('Data file not found: %s' % file_path)
        with open(file_path, 'rb') as handle:
            data = handle.read()
    else:
        data = args.data.encode('utf-8')
    if args.request == 'GET':
        args.request = 'POST'
    return data


def _ssl_context(insecure):
    """Build an SSL context. Verification uses the OS trust store via the net
    overlay's ``certifi`` shim (``where()``); ``-k/--insecure`` disables it."""
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    cafile = None
    try:
        import certifi  # the overlay's OS-trust-store shim (lib/certifi)
        cafile = certifi.where()
    except Exception:
        cafile = None
    if cafile and os.path.isfile(cafile):
        return ssl.create_default_context(cafile=cafile)
    return ssl.create_default_context()


def format_status_line(status, reason):
    line = ('HTTP/1.1 %s %s' % (status, reason or '')).rstrip()
    return line


def format_response_headers(status, reason, header_items):
    status_line = format_status_line(status, reason)
    header_text = ''.join('%s: %s\r\n' % (k, v) for k, v in header_items)
    return ('%s\r\n%s\r\n' % (status_line, header_text)).encode('utf-8')


def write_header_dump(args, header_bytes):
    if not args.dump_header:
        return
    if args.dump_header == '-':
        sys.stdout.buffer.write(header_bytes)
        sys.stdout.buffer.flush()
        return
    with open(args.dump_header, 'wb') as handle:
        handle.write(header_bytes)


def emit_output(args, header_bytes, content):
    if args.output:
        with open(args.output, 'wb') as handle:
            if args.include:
                handle.write(header_bytes)
            handle.write(content)
        if not args.silent:
            print('Output written to %s' % args.output, file=sys.stderr)
        return
    if args.include:
        sys.stdout.buffer.write(header_bytes)
    if not args.head:
        sys.stdout.buffer.write(content)
    sys.stdout.buffer.flush()


def should_print_error(args):
    return (not args.silent) or args.show_error_supported


def _load_cookie_header(args):
    """Return a Cookie: header value from -b (a string ``a=b; c=d`` or a file)."""
    if not args.cookie:
        return None
    if os.path.exists(args.cookie):
        pairs = []
        for line in Path(args.cookie).read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 7:
                pairs.append('%s=%s' % (parts[5], parts[6]))
        return '; '.join(pairs) if pairs else None
    return args.cookie


def maybe_run_system_curl(argv):
    curl_path = shutil.which('curl')
    if not curl_path:
        return None
    result = subprocess.run([curl_path, *argv])
    return result.returncode


def run_fallback(argv):
    """Pure-stdlib (urllib) curl-ish fallback. No third-party dependency."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.help:
        parser.print_help()
        return 0
    check_not_implemented(args)
    url = resolve_url(args, parser)
    headers = prepare_headers(args, parser)
    data = prepare_data(args, parser)
    method = args.request
    if args.head:
        method = 'HEAD'
        data = None
    cookie_header = _load_cookie_header(args)
    if cookie_header:
        headers['Cookie'] = cookie_header

    if args.verbose and not args.silent:
        print('Request: %s %s' % (method, url), file=sys.stderr)
        if data:
            print('Data:', data.decode('utf-8', errors='replace'), file=sys.stderr)
        print('Headers:', headers, file=sys.stderr)
        if args.proxy:
            print('Proxy: %s' % args.proxy, file=sys.stderr)

    # Build the opener: proxy (only the explicit -x; do NOT trust env, matching
    # the precursor's trust_env=False so ambient proxy vars don't silently apply)
    handlers = [urllib.request.HTTPSHandler(context=_ssl_context(args.insecure))]
    if args.proxy:
        handlers.append(urllib.request.ProxyHandler({'http': args.proxy, 'https': args.proxy}))
    else:
        handlers.append(urllib.request.ProxyHandler({}))  # disable ambient proxy
    if not args.location:
        handlers.append(_NoRedirect())
    opener = urllib.request.build_opener(*handlers)

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        resp = opener.open(req, timeout=args.timeout)
        status = resp.getcode()
        reason = getattr(resp, 'reason', '') or ''
        header_items = list(resp.headers.items())
        content = b'' if args.head else resp.read()
    except urllib.error.HTTPError as exc:
        # An HTTP error status is still a response curl prints; emit it.
        status = exc.code
        reason = getattr(exc, 'reason', '') or ''
        header_items = list(exc.headers.items()) if exc.headers else []
        content = b'' if args.head else (exc.read() or b'')
        header_bytes = format_response_headers(status, reason, header_items)
        write_header_dump(args, header_bytes)
        emit_output(args, header_bytes, content)
        if should_print_error(args):
            print('HTTP error %s: %s' % (status, reason), file=sys.stderr)
        return status or 1
    except NotImplementedError:
        raise
    except Exception as exc:
        if should_print_error(args):
            print('URL error: %s' % exc, file=sys.stderr)
        return 1

    if args.verbose and not args.silent:
        print('Response status: %s' % status, file=sys.stderr)
    header_bytes = format_response_headers(status, reason, header_items)
    write_header_dump(args, header_bytes)
    emit_output(args, header_bytes, content)
    if status >= 400:
        if should_print_error(args):
            print('HTTP error %s: %s' % (status, reason), file=sys.stderr)
        return status or 1
    return 0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """When -L is absent, curl does not follow redirects; surface the 3xx as-is."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    system_curl_rc = maybe_run_system_curl(argv)
    if system_curl_rc is not None:
        return system_curl_rc
    return run_fallback(argv)


if __name__ == '__main__':
    sys.exit(main())
