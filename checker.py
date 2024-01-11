import aiohttp
import aiofiles
import asyncio

from termcolor import cprint
from loguru import logger
from datetime import datetime
from typing import Tuple, List
from eth_account import Account as EthAccount

from storage import Storage
from models import AccountInfo
from twitter import Twitter
from config import THREADS_NUM
from utils import async_retry


@async_retry
async def change_ip(link: str):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(link) as resp:
            if resp.status != 200:
                raise Exception(f'Failed to change ip: Status = {resp.status}. Response = {await resp.text()}')


async def check_account(account_data: Tuple[int, Tuple[str, str, str]]):
    idx, (wallet, proxy, twitter_token) = account_data
    address = EthAccount().from_key(wallet).address
    logger.info(f'{idx}) Processing {address}')

    account_info = AccountInfo(address=address, proxy=proxy, twitter_auth_token=twitter_token)

    if '|' in account_info.proxy:
        change_link = account_info.proxy.split('|')[1]
        await change_ip(change_link)
        logger.info(f'{idx}) Successfully changed ip')

    twitter = Twitter(account_info)
    await twitter.start()

    await twitter.follow('elonmusk')

    return True


async def process_batch(bid: int, batch: List[Tuple[int, Tuple[str, str, str]]], async_func):
    failed = []
    for idx, d in enumerate(batch):
        try:
            await async_func(d)
        except Exception as e:
            e_msg = str(e)
            if 'This account is suspended' in e_msg or 'Your account has been locked' in e_msg:
                failed.append(d)
            if e_msg == '':
                e_msg = ' '
            e_msg_lines = e_msg.splitlines()
            logger.error(f'{d[0]}) Process account error: {e_msg_lines[0]}')
            if len(e_msg_lines) > 1:
                async with aiofiles.open('logs/errors.txt', 'a', encoding='utf-8') as file:
                    await file.write(f'{str(datetime.now())} | {d[0]}) Process account error: {e_msg}')
                    await file.flush()

    return failed


async def process(batches: List[List[Tuple[int, Tuple[str, str, str]]]], async_func):
    tasks = []
    for idx, b in enumerate(batches):
        tasks.append(asyncio.create_task(process_batch(idx, b, async_func)))
    return await asyncio.gather(*tasks)


def main():
    with open('files/wallets.txt', 'r', encoding='utf-8') as file:
        wallets = file.read().splitlines()
        wallets = [w.strip() for w in wallets]
    with open('files/proxies.txt', 'r', encoding='utf-8') as file:
        proxies = file.read().splitlines()
        proxies = [p.strip() for p in proxies]
        proxies = [p if '://' in p.split('|')[0] else 'http://' + p for p in proxies]
    with open('files/twitters.txt', 'r', encoding='utf-8') as file:
        twitters = file.read().splitlines()
        twitters = [t.strip() for t in twitters]

    if len(wallets) != len(proxies):
        logger.error('Proxies count does not match wallets count')
        return
    if len(wallets) != len(twitters):
        logger.error('Twitter count does not match wallets count')
        return

    def get_batches(threads: int = THREADS_NUM):
        _data = list(enumerate(list(zip(wallets, proxies, twitters)), start=1))
        _batches: List[List[Tuple[int, Tuple[str, str, str]]]] = [[] for _ in range(threads)]
        for _idx, d in enumerate(_data):
            _batches[_idx % threads].append(d)
        return _batches

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(process(get_batches(), check_account))

    failed_twitter = set()
    for result in results:
        for r in result:
            failed_twitter.add(r[1][2])

    storage = Storage('storage/data.json')
    storage.init()

    failed_cnt = 0

    print()

    open('results/working_wallets.txt', 'w', encoding='utf-8').close()
    open('results/working_proxies.txt', 'w', encoding='utf-8').close()
    open('results/working_twitters.txt', 'w', encoding='utf-8').close()
    for wallet, proxy, twitter in zip(wallets, proxies, twitters):
        if twitter in failed_twitter:
            failed_cnt += 1
            address = EthAccount().from_key(wallet).address
            logger.info(f'Removed for address {address} twitter token {twitter}, proxy {proxy}')
            storage.remove(address)
            continue
        with open('results/working_wallets.txt', 'a', encoding='utf-8') as file:
            file.write(f'{wallet}\n')
        with open('results/working_proxies.txt', 'a', encoding='utf-8') as file:
            file.write(f'{proxy}\n')
        with open('results/working_twitters.txt', 'a', encoding='utf-8') as file:
            file.write(f'{twitter}\n')

    logger.info(f'Total failed count: {failed_cnt}')

    storage.save()

    print()


if __name__ == '__main__':
    cprint('###############################################################', 'cyan')
    cprint('#################', 'cyan', end='')
    cprint(' https://t.me/thelaziestcoder ', 'magenta', end='')
    cprint('################', 'cyan')
    cprint('#################', 'cyan', end='')
    cprint(' https://t.me/thelaziestcoder ', 'magenta', end='')
    cprint('################', 'cyan')
    cprint('#################', 'cyan', end='')
    cprint(' https://t.me/thelaziestcoder ', 'magenta', end='')
    cprint('################', 'cyan')
    cprint('###############################################################\n', 'cyan')
    main()
