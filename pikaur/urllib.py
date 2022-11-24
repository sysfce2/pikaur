import gzip
import json
import socket
from time import sleep
from typing import Any
from urllib import request
from urllib.error import URLError

from .args import parse_args
from .config import PikaurConfig
from .exceptions import SysExit
from .i18n import translate
from .pprint import ColorsHighlight, color_line, print_error, print_stderr
from .prompt import ask_to_continue


DEFAULT_WEB_ENCODING = 'utf-8'
NOCONFIRM_RETRY_INTERVAL = 3


def read_bytes_from_url(url: str, optional: bool = False) -> bytes:
    args = parse_args()
    if args.print_commands:
        print_stderr(
            color_line('=> ', ColorsHighlight.cyan) + f'GET {url}'
        )
    req = request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    try:
        with request.urlopen(req) as response:  # nosec B310
            result_bytes: bytes = response.read()
            return result_bytes
    except URLError as exc:
        print_error(f'GET {url}')
        print_error('urllib: ' + str(exc.reason))
        if optional:
            return b''
        if ask_to_continue(translate('Do you want to retry?')):
            if args.noconfirm:
                print_stderr(
                    translate('Sleeping for {} seconds...').format(
                        NOCONFIRM_RETRY_INTERVAL
                    )
                )
                sleep(NOCONFIRM_RETRY_INTERVAL)
            return read_bytes_from_url(url, optional=optional)
        raise SysExit(102) from exc


def get_unicode_from_url(url: str, optional: bool = False) -> str:
    result_bytes = read_bytes_from_url(url, optional=optional)
    return result_bytes.decode(DEFAULT_WEB_ENCODING)


def get_json_from_url(url: str) -> Any:
    result_json = json.loads(get_unicode_from_url(url))
    return result_json


def get_gzip_from_url(url: str) -> str:
    result_bytes = read_bytes_from_url(url)
    try:
        decompressed_bytes_response = gzip.decompress(result_bytes)
    except EOFError as exc:
        print_error(f'GET {url}')
        print_error('urllib: ' + str(exc))
        if ask_to_continue(translate('Do you want to retry?'), default_yes=False):
            return get_gzip_from_url(url)
        raise SysExit(102) from exc
    text_response = decompressed_bytes_response.decode(DEFAULT_WEB_ENCODING)
    return text_response


class ProxyInitSocks5Error(Exception):
    pass


def init_proxy() -> None:
    net_config = PikaurConfig().network

    socks_proxy_addr = net_config.Socks5Proxy.get_str()
    if socks_proxy_addr:  # pragma: no cover
        port = 1080
        idx = socks_proxy_addr.find(':')
        if idx >= 0:
            port = int(socks_proxy_addr[idx + 1:])
            socks_proxy_addr = socks_proxy_addr[:idx]

        try:
            import socks  # type: ignore[import]  # pylint: disable=import-outside-toplevel
        except ImportError as exc:
            raise ProxyInitSocks5Error(
                translate("pikaur requires python-pysocks to use a socks5 proxy.")
            ) from exc
        socks.set_default_proxy(socks.PROXY_TYPE_SOCKS5, socks_proxy_addr, port)
        socket.socket = socks.socksocket  # type: ignore[misc]

    http_proxy_addr = net_config.AurHttpProxy.get_str()
    https_proxy_addr = net_config.AurHttpsProxy.get_str()
    if http_proxy_addr or https_proxy_addr:
        proxies = {}
        if http_proxy_addr:
            proxies['http'] = http_proxy_addr
        if https_proxy_addr:
            proxies['https'] = https_proxy_addr
        proxy_support = request.ProxyHandler(proxies)
        opener = request.build_opener(proxy_support)
        request.install_opener(opener)


def wrap_proxy_env(cmd: list[str]) -> list[str]:
    """Add env with proxy to command line args."""
    net_config = PikaurConfig().network
    http_proxy_addr = net_config.AurHttpProxy.get_str()
    https_proxy_addr = net_config.AurHttpsProxy.get_str()
    if not (http_proxy_addr or https_proxy_addr):
        return cmd
    proxy_prefix = ['env', ]
    if http_proxy_addr:
        proxy_prefix.append(f'HTTP_PROXY={http_proxy_addr}')
    if https_proxy_addr:
        proxy_prefix.append(f'HTTPS_PROXY={https_proxy_addr}')
    return proxy_prefix + cmd
