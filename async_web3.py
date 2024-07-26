import logging
import os
import threading
from typing import Any, Dict, Iterable, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor

from aiohttp import ClientSession, ClientResponse, ClientTimeout
from aiohttp_socks import ProxyConnector
from eth_typing import URI
from eth_utils import to_dict

from web3.types import AsyncMiddleware, RPCEndpoint, RPCResponse
from web3.datastructures import NamedElementOnion
from web3.middleware.exception_retry_request import async_http_retry_request_middleware
from web3.providers.async_base import AsyncJSONBaseProvider
from web3.utils.caching import SimpleCache
from web3._utils.async_caching import async_lock
from web3._utils.caching import generate_cache_key
from web3._utils.request import _async_close_evicted_sessions

from vars import USER_AGENT
from config import DISABLE_SSL


logger = logging.getLogger(__name__)
DEFAULT_TIMEOUT = 10


def get_default_http_endpoint() -> URI:
    return URI(os.environ.get("WEB3_HTTP_PROVIDER_URI", "http://localhost:8545"))


def construct_user_agent(class_name: str) -> str:
    return USER_AGENT


_async_session_cache = SimpleCache()
_async_session_cache_lock = threading.Lock()
_async_session_pool = ThreadPoolExecutor(max_workers=1)


async def close_all_sessions():
    for _, sess in _async_session_cache.items():
        await sess.close()


async def async_cache_and_return_session_with_proxy(
    endpoint_uri: URI,
    proxy: Optional[str],
    session: Optional[ClientSession] = None,
) -> ClientSession:
    # cache key should have a unique thread identifier
    cache_key = generate_cache_key(f"{threading.get_ident()}:{endpoint_uri}:{proxy if proxy else ''}")

    evicted_items = None
    async with async_lock(_async_session_pool, _async_session_cache_lock):
        if cache_key not in _async_session_cache:
            if session is None:
                conn = ProxyConnector.from_url(proxy) if proxy else None
                session = ClientSession(connector=conn, raise_for_status=True)

            cached_session, evicted_items = _async_session_cache.cache(
                cache_key, session
            )
            logger.debug(f"Async session cached: {endpoint_uri}, {cached_session}")

        else:
            # get the cached session
            cached_session = _async_session_cache.get_cache_entry(cache_key)
            session_is_closed = cached_session.closed
            session_loop_is_closed = cached_session._loop.is_closed()

            warning = (
                "Async session was closed"
                if session_is_closed
                else "Loop was closed for async session"
                if session_loop_is_closed
                else None
            )
            if warning:
                logger.debug(
                    f"{warning}: {endpoint_uri}, {cached_session}. "
                    f"Creating and caching a new async session for uri."
                )

                _async_session_cache._data.pop(cache_key)
                if not session_is_closed:
                    # if loop was closed but not the session, close the session
                    await cached_session.close()
                logger.debug(
                    f"Async session closed and evicted from cache: {cached_session}"
                )

                # replace stale session with a new session at the cache key
                _conn = ProxyConnector.from_url(proxy) if proxy else None
                _session = ClientSession(connector=_conn, raise_for_status=True)
                cached_session, evicted_items = _async_session_cache.cache(
                    cache_key, _session
                )
                logger.debug(f"Async session cached: {endpoint_uri}, {cached_session}")

    if evicted_items is not None:
        # At this point the evicted sessions are already popped out of the cache and
        # just stored in the `evicted_sessions` dict. So we can kick off a future task
        # to close them and it should be safe to pop out of the lock here.
        evicted_sessions = evicted_items.values()
        for evicted_session in evicted_sessions:
            logger.debug(
                "Async session cache full. Session evicted from cache: "
                f"{evicted_session}",
            )
        # Kick off a future task, in a separate thread, to close the evicted
        # sessions. In the case that the cache filled very quickly and some
        # sessions have been evicted before their original request has been made,
        # we set the timer to a bit more than the `DEFAULT_TIMEOUT` for a call. This
        # should make it so that any call from an evicted session can still be made
        # before the session is closed.
        threading.Timer(
            DEFAULT_TIMEOUT + 0.1,
            _async_close_evicted_sessions,
            args=[evicted_sessions],
        ).start()

    return cached_session


async def async_get_response_from_post_request_with_proxy(
    endpoint_uri: URI, proxy: Optional[str], *args: Any, **kwargs: Any
) -> ClientResponse:
    kwargs.setdefault("timeout", ClientTimeout(DEFAULT_TIMEOUT))
    session = await async_cache_and_return_session_with_proxy(endpoint_uri, proxy)
    response = await session.post(endpoint_uri, *args, **kwargs)
    return response


async def async_make_post_request_with_proxy(
    endpoint_uri: URI, proxy: Optional[str], data: Union[bytes, Dict[str, Any]], *args: Any, **kwargs: Any
) -> bytes:
    response = await async_get_response_from_post_request_with_proxy(
        endpoint_uri, proxy, data=data, *args, **kwargs
    )
    response.raise_for_status()
    return await response.read()


class AsyncHTTPProviderWithProxy(AsyncJSONBaseProvider):
    logger = logging.getLogger("web3.providers.AsyncHTTPProvider")
    endpoint_uri = None
    _request_kwargs = None
    # type ignored b/c conflict with _middlewares attr on AsyncBaseProvider
    _middlewares: Tuple[AsyncMiddleware, ...] = NamedElementOnion([(async_http_retry_request_middleware, "http_retry_request")])  # type: ignore # noqa: E501

    def __init__(
        self,
        endpoint_uri: Optional[Union[URI, str]] = None,
        proxy: Optional[str] = None,
        request_kwargs: Optional[Any] = None,
    ) -> None:
        if endpoint_uri is None:
            self.endpoint_uri = get_default_http_endpoint()
        else:
            self.endpoint_uri = URI(endpoint_uri)

        self.proxy = proxy

        self._request_kwargs = request_kwargs or {}
        if DISABLE_SSL:
            self._request_kwargs.update({'ssl': False})

        super().__init__()

    async def close(self):
        session = await async_cache_and_return_session_with_proxy(self.endpoint_uri, self.proxy)
        await session.close()

    async def cache_async_session(self, session: ClientSession) -> ClientSession:
        return await async_cache_and_return_session_with_proxy(self.endpoint_uri, self.proxy, session)

    def __str__(self) -> str:
        return f"RPC connection {self.endpoint_uri}"

    @to_dict
    def get_request_kwargs(self) -> Iterable[Tuple[str, Any]]:
        if "headers" not in self._request_kwargs:
            yield "headers", self.get_request_headers()
        for key, value in self._request_kwargs.items():
            yield key, value

    def get_request_headers(self) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "User-Agent": construct_user_agent(str(type(self))),
        }

    async def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        self.logger.debug(
            f"Making request HTTP. URI: {self.endpoint_uri}, Method: {method}"
        )
        request_data = self.encode_rpc_request(method, params)
        raw_response = await async_make_post_request_with_proxy(
            self.endpoint_uri, self.proxy, request_data, **self.get_request_kwargs()
        )
        response = self.decode_rpc_response(raw_response)
        self.logger.debug(
            f"Getting response HTTP. URI: {self.endpoint_uri}, "
            f"Method: {method}, Response: {response}"
        )
        return response
