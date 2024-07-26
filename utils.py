import random
import asyncio
import aiofiles
from retry import retry
from web3 import AsyncWeb3
from typing import cast
from loguru import logger
from datetime import datetime
from async_web3 import AsyncHTTPProviderWithProxy
from config import RPC, MAX_TRIES
from aiohttp import ClientResponse


def is_empty(val):
    if val is None:
        return True
    if type(val) == str:
        return val == ''
    return False


async def wait_a_bit(x=1):
    await asyncio.sleep(random.uniform(0.5, 1) * x)


@retry(tries=MAX_TRIES, delay=1.5, max_delay=10, backoff=2, jitter=(0, 1))
def get_w3(proxy: str = None, rpc: str = None):
    if proxy and '|' in proxy:
        proxy = proxy.split('|')[0]
    proxy = None if is_empty(proxy) else proxy
    return AsyncWeb3(AsyncHTTPProviderWithProxy(RPC if rpc is None else rpc, proxy))


def to_bytes(hex_str):
    return AsyncWeb3.to_bytes(hexstr=hex_str)


async def close_w3(w3: AsyncWeb3):
    if isinstance(w3.manager.provider, AsyncHTTPProviderWithProxy):
        await cast(AsyncHTTPProviderWithProxy, w3.manager.provider).close()


async def handle_response(resp_raw, acceptable_statuses=None, resp_handler=None, with_text=False):
    if acceptable_statuses and len(acceptable_statuses) > 0:
        if resp_raw.status_code not in acceptable_statuses:
            raise Exception(f'Bad status code [{resp_raw.status_code}]: Response = {resp_raw.text}')
    try:
        if resp_handler is not None:
            if with_text:
                return resp_handler(resp_raw.text)
            else:
                return resp_handler(resp_raw.json())
        return
    except Exception as e:
        raise Exception(f'{str(e)}: Status = {resp_raw.status_code}. Response = {resp_raw.text}')


async def handle_aio_response(resp_raw: ClientResponse, acceptable_statuses=None, resp_handler=None, with_text=False):
    if acceptable_statuses and len(acceptable_statuses) > 0:
        if resp_raw.status not in acceptable_statuses:
            raise Exception(f'Bad status code [{resp_raw.status}]: Response = {await resp_raw.text()}')
    try:
        if resp_handler is not None:
            if with_text:
                return resp_handler(await resp_raw.text())
            else:
                return resp_handler(await resp_raw.json())
        return
    except Exception as e:
        raise Exception(f'{str(e)}: Status = {resp_raw.status}. Response = {await resp_raw.text()}')


def async_retry(async_func):
    async def wrapper(*args, **kwargs):
        tries, delay = MAX_TRIES, 1.5
        while tries > 0:
            try:
                return await async_func(*args, **kwargs)
            except Exception:
                tries -= 1
                if tries <= 0:
                    raise
                await asyncio.sleep(delay)

                delay *= 2
                delay += random.uniform(0, 1)
                delay = min(delay, 10)

    return wrapper


async def log_long_exc(idx, msg, e, warning=False):
    e_msg = str(e)
    if e_msg == '':
        e_msg = ' '
    e_msg_lines = e_msg.splitlines()
    log = logger.warning if warning else logger.error
    log(f'{idx}) {msg}: {e_msg_lines[0]}')
    if len(e_msg_lines) > 1:
        async with aiofiles.open('logs/errors.txt', 'a', encoding='utf-8') as file:
            await file.write(f'{str(datetime.now())} | {idx}) Process account error: {e_msg}')
            await file.flush()
