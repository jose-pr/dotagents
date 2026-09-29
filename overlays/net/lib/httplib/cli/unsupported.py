"""Flags curl has and the fallback does not honour. Each is declared -- hidden
from ``--help`` -- so it parses, then refused out loud (``NotImplementedError``,
exit 2): the fallback must never silently do the wrong thing for a flag it
does not actually honour. With a real curl on PATH none of this applies --
real curl runs the command."""
import argparse
from typing import List, Optional

from ._duho import NS, Arg
from .args import Group

_HIDDEN = NS(help=argparse.SUPPRESS)

#: The fields below; a set one is refused.
UNSUPPORTED_ARGS = [
    'any', 'append', 'cert_status', 'ciphers', 'config', 'continue_at', 'crlf',
    'crlfile', 'data_ascii', 'delegation', 'digest', 'disable_eprt',
    'disable_epsv', 'dns_interface', 'dns_ipv4_addr', 'dns_ipv6_addr',
    'dns_servers', 'doh_url', 'egd_file', 'engine', 'expect100_timeout',
    'fail_early', 'false_start', 'ftp_account', 'ftp_alternative_to_user',
    'ftp_create_dirs', 'ftp_method', 'ftp_pasv', 'ftp_skip_pasv_ip',
    'ftp_ssl_ccc_mode', 'ftp_ssl_ccc', 'ftp_ssl_control',
    'happy_eyeballs_timeout_ms', 'haproxy_protocol', 'hostpubmd5', 'http1_0',
    'http2_prior_knowledge', 'http2', 'http3', 'ignore_content_length',
    'interface', 'ip_resolve', 'ipv4', 'ipv6', 'junk_session_cookies',
    'keepalive_time', 'krb', 'libcurl', 'limit_rate', 'list_only',
    'local_port', 'location_trusted', 'login_options', 'mail_auth',
    'mail_from', 'mail_rcpt_allowfails', 'mail_rcpt', 'max_filesize',
    'metalink', 'negotiate', 'netrc_file', 'netrc_optional', 'netrc', 'next',
    'no_alpn', 'no_keepalive', 'no_npn', 'no_progress_bar', 'no_sessionid',
    'ntlm_wb', 'ntlm', 'parallel_immediate', 'parallel_max', 'parallel',
    'path_as_is', 'pinnedpubkey', 'post301', 'post302', 'post303', 'preproxy',
    'proto_default', 'proto_redir', 'proto', 'proxy_anyauth', 'proxy_basic',
    'proxy_cacert', 'proxy_capath', 'proxy_cert_type', 'proxy_cert',
    'proxy_ciphers', 'proxy_crlfile', 'proxy_digest', 'proxy_header',
    'proxy_insecure', 'proxy_key_type', 'proxy_key', 'proxy_negotiate',
    'proxy_ntlm', 'proxy_pass', 'proxy_pinnedpubkey', 'proxy_service_name',
    'proxy_ssl_allow_beast', 'proxy_ssl_auto_client_cert',
    'proxy_tls13_ciphers', 'proxy_tlsauthtype', 'proxy_tlspassword',
    'proxy_tlsuser', 'proxy_tlsv1', 'proxytunnel', 'pubkey', 'quote',
    'random_file', 'raw', 'remote_header_name', 'remote_time',
    'request_target', 'resolve', 'sasl_authzid', 'sasl_ir', 'service_name',
    'show_headers', 'socks4', 'socks4a', 'socks5_basic', 'socks5_gssapi_nec',
    'socks5_gssapi_service', 'socks5_gssapi', 'socks5_hostname', 'socks5',
    'speed_limit', 'speed_time', 'ssl_allow_beast', 'ssl_auto_client_cert',
    'ssl_no_revoke', 'ssl_reqd', 'ssl_revoke_best_effort', 'ssl', 'sslv2',
    'sslv3', 'stderr', 'styled_output', 'suppress_connect_headers',
    'tcp_fastopen', 'tcp_nodelay', 'telnet_option', 'tftp_blksize',
    'tftp_no_options', 'time_cond', 'tls_max', 'tls13_ciphers', 'tlsauthtype',
    'tlspassword', 'tlsuser', 'tlsv1_0', 'tlsv1_1', 'tlsv1_2', 'tlsv1_3',
    'tlsv1', 'tr_encoding', 'trace_ascii', 'trace_time', 'trace',
    'unix_socket', 'use_ascii', 'variable', 'vsock', 'xattr',
]


class UnsupportedArgs(Group):
    """Recognised-but-unsupported flags, refused by ``_check``."""

    any: Arg[bool, _HIDDEN] = False
    ('--any',)

    append: Arg[bool, _HIDDEN] = False
    ('--append',)

    cert_status: Arg[bool, _HIDDEN] = False
    ('--cert-status',)

    ciphers: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='CIPHERS')] = None
    ('--ciphers',)

    config: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='CONFIG')] = None
    ('--config',)

    continue_at: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='OFFSET')] = None
    ('--continue-at',)

    crlf: Arg[bool, _HIDDEN] = False
    ('--crlf',)

    crlfile: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--crlfile',)

    data_ascii: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='DATA')] = None
    ('--data-ascii',)

    delegation: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='LEVEL')] = None
    ('--delegation',)

    digest: Arg[bool, _HIDDEN] = False
    ('--digest',)

    disable_eprt: Arg[bool, _HIDDEN] = False
    ('--disable-eprt',)

    disable_epsv: Arg[bool, _HIDDEN] = False
    ('--disable-epsv',)

    dns_interface: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='INTERFACE')] = None
    ('--dns-interface',)

    dns_ipv4_addr: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='ADDRESS')] = None
    ('--dns-ipv4-addr',)

    dns_ipv6_addr: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='ADDRESS')] = None
    ('--dns-ipv6-addr',)

    dns_servers: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='ADDRESSES')] = None
    ('--dns-servers',)

    doh_url: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='URL')] = None
    ('--doh-url',)

    egd_file: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--egd-file',)

    engine: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='ENGINE')] = None
    ('--engine',)

    expect100_timeout: Arg[Optional[float], _HIDDEN] = None
    ('--expect100-timeout',)

    fail_early: Arg[bool, _HIDDEN] = False
    ('--fail-early',)

    false_start: Arg[bool, _HIDDEN] = False
    ('--false-start',)

    ftp_account: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='DATA')] = None
    ('--ftp-account',)

    ftp_alternative_to_user: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='COMMAND')] = None
    ('--ftp-alternative-to-user',)

    ftp_create_dirs: Arg[bool, _HIDDEN] = False
    ('--ftp-create-dirs',)

    ftp_method: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='METHOD')] = None
    ('--ftp-method',)

    ftp_pasv: Arg[bool, _HIDDEN] = False
    ('--ftp-pasv',)

    ftp_skip_pasv_ip: Arg[bool, _HIDDEN] = False
    ('--ftp-skip-pasv-ip',)

    ftp_ssl_ccc_mode: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='MODE')] = None
    ('--ftp-ssl-ccc-mode',)

    ftp_ssl_ccc: Arg[bool, _HIDDEN] = False
    ('--ftp-ssl-ccc',)

    ftp_ssl_control: Arg[bool, _HIDDEN] = False
    ('--ftp-ssl-control',)

    happy_eyeballs_timeout_ms: Arg[Optional[int], _HIDDEN] = None
    ('--happy-eyeballs-timeout-ms',)

    haproxy_protocol: Arg[bool, _HIDDEN] = False
    ('--haproxy-protocol',)

    hostpubmd5: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='MD5')] = None
    ('--hostpubmd5',)

    http1_0: Arg[bool, _HIDDEN] = False
    ('--http1.0',)

    http2_prior_knowledge: Arg[bool, _HIDDEN] = False
    ('--http2-prior-knowledge',)

    http2: Arg[bool, _HIDDEN] = False
    ('--http2',)

    http3: Arg[bool, _HIDDEN] = False
    ('--http3',)

    ignore_content_length: Arg[bool, _HIDDEN] = False
    ('--ignore-content-length',)

    interface: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='INTERFACE')] = None
    ('--interface',)

    ip_resolve: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='RESOLVE')] = None
    ('--ip-resolve',)

    ipv4: Arg[bool, _HIDDEN] = False
    ('--ipv4',)

    ipv6: Arg[bool, _HIDDEN] = False
    ('--ipv6',)

    junk_session_cookies: Arg[bool, _HIDDEN] = False
    ('--junk-session-cookies',)

    keepalive_time: Arg[Optional[int], _HIDDEN] = None
    ('--keepalive-time',)

    krb: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='LEVEL')] = None
    ('--krb',)

    libcurl: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--libcurl',)

    limit_rate: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='RATE')] = None
    ('--limit-rate',)

    list_only: Arg[bool, _HIDDEN] = False
    ('--list-only',)

    local_port: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='RANGE')] = None
    ('--local-port',)

    location_trusted: Arg[bool, _HIDDEN] = False
    ('--location-trusted',)

    login_options: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='OPTIONS')] = None
    ('--login-options',)

    mail_auth: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='AUTH')] = None
    ('--mail-auth',)

    mail_from: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FROM')] = None
    ('--mail-from',)

    mail_rcpt_allowfails: Arg[bool, _HIDDEN] = False
    ('--mail-rcpt-allowfails',)

    mail_rcpt: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='RCPT')] = None
    ('--mail-rcpt',)

    max_filesize: Arg[Optional[int], _HIDDEN] = None
    ('--max-filesize',)

    metalink: Arg[bool, _HIDDEN] = False
    ('--metalink',)

    negotiate: Arg[bool, _HIDDEN] = False
    ('--negotiate',)

    netrc_file: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--netrc-file',)

    netrc_optional: Arg[bool, _HIDDEN] = False
    ('--netrc-optional',)

    netrc: Arg[bool, _HIDDEN] = False
    ('-n', '--netrc')

    next: Arg[bool, _HIDDEN] = False
    ('--next',)

    no_alpn: Arg[bool, _HIDDEN] = False
    ('--no-alpn',)

    no_keepalive: Arg[bool, _HIDDEN] = False
    ('--no-keepalive',)

    no_npn: Arg[bool, _HIDDEN] = False
    ('--no-npn',)

    no_progress_bar: Arg[bool, _HIDDEN] = False
    ('--no-progress-bar',)

    no_sessionid: Arg[bool, _HIDDEN] = False
    ('--no-sessionid',)

    ntlm_wb: Arg[bool, _HIDDEN] = False
    ('--ntlm-wb',)

    ntlm: Arg[bool, _HIDDEN] = False
    ('--ntlm',)

    parallel_immediate: Arg[bool, _HIDDEN] = False
    ('--parallel-immediate',)

    parallel_max: Arg[Optional[int], _HIDDEN] = None
    ('--parallel-max',)

    parallel: Arg[bool, _HIDDEN] = False
    ('--parallel',)

    path_as_is: Arg[bool, _HIDDEN] = False
    ('--path-as-is',)

    pinnedpubkey: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HASHES')] = None
    ('--pinnedpubkey',)

    post301: Arg[bool, _HIDDEN] = False
    ('--post301',)

    post302: Arg[bool, _HIDDEN] = False
    ('--post302',)

    post303: Arg[bool, _HIDDEN] = False
    ('--post303',)

    preproxy: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PROXY')] = None
    ('--preproxy',)

    proto_default: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PROTO')] = None
    ('--proto-default',)

    proto_redir: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PROTOCOLS')] = None
    ('--proto-redir',)

    proto: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PROTOCOLS')] = None
    ('--proto',)

    proxy_anyauth: Arg[bool, _HIDDEN] = False
    ('--proxy-anyauth',)

    proxy_basic: Arg[bool, _HIDDEN] = False
    ('--proxy-basic',)

    proxy_cacert: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--proxy-cacert',)

    proxy_capath: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='DIR')] = None
    ('--proxy-capath',)

    proxy_cert_type: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='TYPE')] = None
    ('--proxy-cert-type',)

    proxy_cert: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='CERT')] = None
    ('--proxy-cert',)

    proxy_ciphers: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='LIST')] = None
    ('--proxy-ciphers',)

    proxy_crlfile: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--proxy-crlfile',)

    proxy_digest: Arg[bool, _HIDDEN] = False
    ('--proxy-digest',)

    proxy_header: Arg[Optional[List[str]], _HIDDEN] = None
    ('--proxy-header',)

    proxy_insecure: Arg[bool, _HIDDEN] = False
    ('--proxy-insecure',)

    proxy_key_type: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='TYPE')] = None
    ('--proxy-key-type',)

    proxy_key: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='KEY')] = None
    ('--proxy-key',)

    proxy_negotiate: Arg[bool, _HIDDEN] = False
    ('--proxy-negotiate',)

    proxy_ntlm: Arg[bool, _HIDDEN] = False
    ('--proxy-ntlm',)

    proxy_pass: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PASS')] = None
    ('--proxy-pass',)

    proxy_pinnedpubkey: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HASHES')] = None
    ('--proxy-pinnedpubkey',)

    proxy_service_name: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='NAME')] = None
    ('--proxy-service-name',)

    proxy_ssl_allow_beast: Arg[bool, _HIDDEN] = False
    ('--proxy-ssl-allow-beast',)

    proxy_ssl_auto_client_cert: Arg[bool, _HIDDEN] = False
    ('--proxy-ssl-auto-client-cert',)

    proxy_tls13_ciphers: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='CIPHERS')] = None
    ('--proxy-tls13-ciphers',)

    proxy_tlsauthtype: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='TYPE')] = None
    ('--proxy-tlsauthtype',)

    proxy_tlspassword: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='STRING')] = None
    ('--proxy-tlspassword',)

    proxy_tlsuser: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='USER')] = None
    ('--proxy-tlsuser',)

    proxy_tlsv1: Arg[bool, _HIDDEN] = False
    ('--proxy-tlsv1',)

    proxytunnel: Arg[bool, _HIDDEN] = False
    ('--proxytunnel',)

    pubkey: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='KEY')] = None
    ('--pubkey',)

    quote: Arg[Optional[List[str]], _HIDDEN] = None
    ('-Q', '--quote')

    random_file: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--random-file',)

    raw: Arg[bool, _HIDDEN] = False
    ('--raw',)

    remote_header_name: Arg[bool, _HIDDEN] = False
    ('--remote-header-name',)

    remote_time: Arg[bool, _HIDDEN] = False
    ('--remote-time',)

    request_target: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PATH')] = None
    ('--request-target',)

    resolve: Arg[Optional[List[str]], _HIDDEN] = None
    ('--resolve',)

    sasl_authzid: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='IDENTITY')] = None
    ('--sasl-authzid',)

    sasl_ir: Arg[bool, _HIDDEN] = False
    ('--sasl-ir',)

    service_name: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='NAME')] = None
    ('--service-name',)

    show_headers: Arg[bool, _HIDDEN] = False
    ('--show-headers',)

    socks4: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HOST[:PORT]')] = None
    ('--socks4',)

    socks4a: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HOST[:PORT]')] = None
    ('--socks4a',)

    socks5_basic: Arg[bool, _HIDDEN] = False
    ('--socks5-basic',)

    socks5_gssapi_nec: Arg[bool, _HIDDEN] = False
    ('--socks5-gssapi-nec',)

    socks5_gssapi_service: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='NAME')] = None
    ('--socks5-gssapi-service',)

    socks5_gssapi: Arg[bool, _HIDDEN] = False
    ('--socks5-gssapi',)

    socks5_hostname: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HOST[:PORT]')] = None
    ('--socks5-hostname',)

    socks5: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='HOST[:PORT]')] = None
    ('--socks5',)

    speed_limit: Arg[Optional[int], _HIDDEN] = None
    ('--speed-limit',)

    speed_time: Arg[Optional[int], _HIDDEN] = None
    ('--speed-time',)

    ssl_allow_beast: Arg[bool, _HIDDEN] = False
    ('--ssl-allow-beast',)

    ssl_auto_client_cert: Arg[bool, _HIDDEN] = False
    ('--ssl-auto-client-cert',)

    ssl_no_revoke: Arg[bool, _HIDDEN] = False
    ('--ssl-no-revoke',)

    ssl_reqd: Arg[bool, _HIDDEN] = False
    ('--ssl-reqd',)

    ssl_revoke_best_effort: Arg[bool, _HIDDEN] = False
    ('--ssl-revoke-best-effort',)

    ssl: Arg[bool, _HIDDEN] = False
    ('--ssl',)

    sslv2: Arg[bool, _HIDDEN] = False
    ('--sslv2',)

    sslv3: Arg[bool, _HIDDEN] = False
    ('--sslv3',)

    stderr: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--stderr',)

    styled_output: Arg[bool, _HIDDEN] = False
    ('--styled-output',)

    suppress_connect_headers: Arg[bool, _HIDDEN] = False
    ('--suppress-connect-headers',)

    tcp_fastopen: Arg[bool, _HIDDEN] = False
    ('--tcp-fastopen',)

    tcp_nodelay: Arg[bool, _HIDDEN] = False
    ('--tcp-nodelay',)

    telnet_option: Arg[Optional[List[str]], _HIDDEN] = None
    ('--telnet-option',)

    tftp_blksize: Arg[Optional[int], _HIDDEN] = None
    ('--tftp-blksize',)

    tftp_no_options: Arg[bool, _HIDDEN] = False
    ('--tftp-no-options',)

    time_cond: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='TIME')] = None
    ('--time-cond',)

    tls_max: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='VERSION')] = None
    ('--tls-max',)

    tls13_ciphers: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='CIPHERS')] = None
    ('--tls13-ciphers',)

    tlsauthtype: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='TYPE')] = None
    ('--tlsauthtype',)

    tlspassword: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='STRING')] = None
    ('--tlspassword',)

    tlsuser: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='USER')] = None
    ('--tlsuser',)

    tlsv1_0: Arg[bool, _HIDDEN] = False
    ('--tlsv1.0',)

    tlsv1_1: Arg[bool, _HIDDEN] = False
    ('--tlsv1.1',)

    tlsv1_2: Arg[bool, _HIDDEN] = False
    ('--tlsv1.2',)

    tlsv1_3: Arg[bool, _HIDDEN] = False
    ('--tlsv1.3',)

    tlsv1: Arg[bool, _HIDDEN] = False
    ('--tlsv1',)

    tr_encoding: Arg[bool, _HIDDEN] = False
    ('--tr-encoding',)

    trace_ascii: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--trace-ascii',)

    trace_time: Arg[bool, _HIDDEN] = False
    ('--trace-time',)

    trace: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='FILE')] = None
    ('--trace',)

    unix_socket: Arg[Optional[str], NS(help=argparse.SUPPRESS, metavar='PATH')] = None
    ('--unix-socket',)

    use_ascii: Arg[bool, _HIDDEN] = False
    ('--use-ascii',)

    variable: Arg[Optional[List[str]], _HIDDEN] = None
    ('--variable',)

    vsock: Arg[bool, _HIDDEN] = False
    ('--vsock',)

    xattr: Arg[bool, _HIDDEN] = False
    ('--xattr',)

    def _check(self):
        unsupported = ['--%s' % name.replace('_', '-') for name in UNSUPPORTED_ARGS if getattr(self, name, None)]
        if unsupported:
            raise NotImplementedError('Unsupported options: %s' % ', '.join(unsupported))
