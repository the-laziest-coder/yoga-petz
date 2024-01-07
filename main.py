import csv
import random
import aiohttp
import aiofiles
import asyncio

from termcolor import cprint
from loguru import logger
from datetime import datetime
from typing import Tuple, List, Optional
from eth_account import Account as EthAccount

from storage import Storage
from models import AccountInfo, ProcessResult
from twitter import Twitter
from well3 import Well3
from account import Account
from config import DO_TASKS, CHECK_INSIGHTS, CLAIM_DAILY_INSIGHT, CLAIM_RANK_INSIGHTS, \
    WAIT_BETWEEN_ACCOUNTS, THREADS_NUM, AUTO_UPDATE_INVITES, AUTO_UPDATE_INVITES_FROM_FIRST_COUNT, SKIP_FIRST_ACCOUNTS
from utils import wait_a_bit, async_retry


class InvitesHandler:

    def __init__(self, invites: List[str], storage: Storage, addresses: List[str]):
        self.invites = invites
        self.storage = storage
        self.addresses = addresses
        self.lock = asyncio.Lock()

    async def add_invites(self, invites: List[str]):
        async with self.lock:
            self.invites.extend(invites)

    async def get_invite(self) -> Optional[str]:
        async with self.lock:
            if len(self.invites) == 0:
                return None
            return self.invites.pop(0)

    async def update_invites(self):
        try:
            async with self.lock:

                if len(self.invites) != 0:
                    return

                for idx, address in enumerate(self.addresses[:AUTO_UPDATE_INVITES_FROM_FIRST_COUNT], start=1):
                    if idx != 1:
                        await asyncio.sleep(random.uniform(WAIT_BETWEEN_ACCOUNTS[0], WAIT_BETWEEN_ACCOUNTS[1]))

                    account_info = await refresh(f'Updating invites {idx}', address, self.storage)
                    if account_info is None:
                        break

                    logger.info(f'Updating invites {idx}) Added new {len(account_info.invite_codes)}')
                    self.invites.extend(account_info.invite_codes)

                logger.success(f'Invites updated: {len(self.invites)} new')

        except Exception as e:
            raise Exception(f'Update invites failed: {str(e)}')


@async_retry
async def change_ip(link: str):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(link) as resp:
            if resp.status != 200:
                raise Exception(f'Failed to change ip: Status = {resp.status}. Response = {await resp.text()}')


async def refresh(prefix: str, address: str, storage: Storage):
    logger.info(f'{prefix}) {address}')
    account_info = await storage.get_account_info(address)
    if account_info is None:
        return None
    if '|' in account_info.proxy:
        change_link = account_info.proxy.split('|')[1]
        await change_ip(change_link)
        logger.info(f'{prefix}) Successfully changed ip')
    twitter = Twitter(account_info)
    await twitter.start()
    well3 = Well3(f'{prefix}', account_info, twitter)
    if await well3.sign_in_or_start_register_if_needed():
        return None
    account = Account(f'{prefix}', account_info, well3, twitter)
    await account.check_invite_codes()
    await storage.set_account_info(address, account_info)
    return account_info


async def refresh_account(account_data: Tuple[int, Tuple[str, str, str]], storage: Storage, _):
    idx, (wallet, proxy, twitter_token) = account_data
    address = EthAccount().from_key(wallet).address
    await refresh(f'Refreshing account {idx}', address, storage)
    return ProcessResult()


async def process_account(account_data: Tuple[int, Tuple[str, str, str]], storage: Storage, invites: InvitesHandler) \
        -> ProcessResult:

    result = ProcessResult()

    idx, (wallet, proxy, twitter_token) = account_data
    address = EthAccount().from_key(wallet).address
    logger.info(f'{idx}) Processing {address}')

    account_info = await storage.get_account_info(address)
    if account_info is None:
        logger.info(f'{idx}) Account info was not saved before')
        account_info = AccountInfo(address=address, proxy=proxy, twitter_auth_token=twitter_token)
    else:
        logger.info(f'{idx}) Saved account info restored')

    if '|' in account_info.proxy:
        change_link = account_info.proxy.split('|')[1]
        await change_ip(change_link)
        logger.info(f'{idx}) Successfully changed ip')

    twitter = Twitter(account_info)
    await twitter.start()

    well3 = Well3(idx, account_info, twitter)

    logger.info(f'{idx}) Signing in')

    need_invite = await well3.sign_in_or_start_register_if_needed()
    if need_invite:
        while True:
            invite = await invites.get_invite()
            if invite is None:
                if AUTO_UPDATE_INVITES:
                    await invites.update_invites()
                invite = await invites.get_invite()
                if invite is None:
                    raise Exception(f'No invite codes left')
            logger.info(f'{idx}) Entering invite code: {invite}')
            try:
                await well3.use_invite_code(invite)
            except Exception as e:
                if 'Code not found or already used' in str(e):
                    logger.info(f'{idx}) Code already used. Trying another one')
                    continue
                raise
            result.invite_used = True
            break

    logger.info(f'{idx}) Signed in')

    account = Account(idx, account_info, well3, twitter)
    await account.refresh_profile()
    await account.check_invite_codes()

    logger.info(f'{idx}) Profile refreshed')

    if DO_TASKS:
        await account.do_quests()
        await account.refresh_profile()

    if CHECK_INSIGHTS or CLAIM_DAILY_INSIGHT or CLAIM_RANK_INSIGHTS:
        await account.link_wallet_if_needed(wallet)

    try:
        if CLAIM_DAILY_INSIGHT:
            await wait_a_bit(3)
            logger.info(f'{idx}) Starting claim daily insight')
            await account.claim_daily_insight()
        if CLAIM_RANK_INSIGHTS:
            await wait_a_bit(3)
            logger.info(f'{idx}) Starting claim rank insights')
            await account.claim_rank_insights()
    except Exception as e:
        logger.error(f'{idx}) Claim error: {str(e)}')

    if CLAIM_DAILY_INSIGHT or CLAIM_RANK_INSIGHTS:
        await account.refresh_profile()

    if CHECK_INSIGHTS:
        logger.info(f'{idx}) Checking insights')
        await account.check_insights()

    logger.info(f'{idx}) Account stats:\n{account_info.str_stats()}')

    await storage.set_account_info(address, account_info)

    return result


async def process_batch(batch: List[Tuple[int, Tuple[str, str, str]]], storage: Storage, invites: InvitesHandler,
                        async_func) -> int:
    used_invites = 0
    for idx, d in enumerate(batch):
        if idx != 0:
            await asyncio.sleep(random.uniform(WAIT_BETWEEN_ACCOUNTS[0], WAIT_BETWEEN_ACCOUNTS[1]))
        try:
            result = await async_func(d, storage, invites)
            if result.invite_used:
                used_invites += 1
        except Exception as e:
            e_msg = str(e)
            if e_msg == '':
                e_msg = ' '
            e_msg_lines = e_msg.splitlines()
            logger.error(f'{d[0]}) Process account error: {e_msg_lines[0]}')
            if len(e_msg_lines) > 1:
                async with aiofiles.open('logs/errors.txt', 'a', encoding='utf-8') as file:
                    await file.write(f'{str(datetime.now())} | {d[0]}) Process account error: {e_msg}')
                    await file.flush()

    return used_invites


async def process(batches: List[List[Tuple[int, Tuple[str, str, str]]]], storage: Storage, invites: InvitesHandler,
                  async_func):
    tasks = []
    for b in batches:
        tasks.append(asyncio.create_task(process_batch(b, storage, invites, async_func)))
    return await asyncio.gather(*tasks)


def main():
    with open('files/wallets.txt', 'r', encoding='utf-8') as file:
        wallets = file.read().splitlines()
        wallets = [w.strip() for w in wallets]
    with open('files/proxies.txt', 'r', encoding='utf-8') as file:
        proxies = file.read().splitlines()
        proxies = [p.strip() for p in proxies]
        proxies = [p if '://' in p else 'http://' + p for p in proxies]
    with open('files/twitters.txt', 'r', encoding='utf-8') as file:
        twitters = file.read().splitlines()
        twitters = [t.strip() for t in twitters]
    with open('files/invites.txt', 'r', encoding='utf-8') as file:
        invites = file.read().splitlines()
        invites = [i.strip() for i in invites]

    if len(wallets) != len(proxies):
        logger.error('Proxies count does not match wallets count')
        return
    if len(wallets) != len(twitters):
        logger.error('Twitter count does not match wallets count')
        return

    storage = Storage('storage/data.json')
    storage.init()

    addresses = [EthAccount().from_key(w).address for w in wallets]

    invites_handler = InvitesHandler(invites, storage, addresses)

    data = list(enumerate(list(zip(wallets, proxies, twitters)), start=1))[SKIP_FIRST_ACCOUNTS:]

    batches: List[List[Tuple[int, Tuple[str, str, str]]]] = [[] for _ in range(THREADS_NUM)]
    for idx, d in enumerate(data):
        batches[idx % THREADS_NUM].append(d)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(process(batches, storage, invites_handler, process_account))

    used_invites = sum(results)

    print()
    logger.info('Finished. Refreshing accounts profiles')

    loop.run_until_complete(process(batches, storage, invites_handler, refresh_account))

    storage.save()

    print()
    logger.info(f'Used invites: {used_invites}')

    csv_data = [['Address', 'Uncommon', 'Rare', 'Legendary', 'Mythical',
                 'Daily insight', 'Insights to open', 'Pending quests', 'Next breathe', 'Exp', 'Lvl']]
    total = {
        'uncommon': 0,
        'rare': 0,
        'legendary': 0,
        'mythical': 0,
        'daily_claimed': 0,
        'daily_available': 0,
        'to_open': 0,
        'pending': 0,
    }
    all_invite_codes = []
    for w in wallets:
        address = EthAccount().from_key(w).address

        account = storage.get_final_account_info(address)
        if account is None:
            csv_data.append([address])
            continue

        all_invite_codes.extend(account.invite_codes)

        total['uncommon'] += account.insights.get('uncommon', 0)
        total['rare'] += account.insights.get('rare', 0)
        total['legendary'] += account.insights.get('legendary', 0)
        total['mythical'] += account.insights.get('mythical', 0)
        if account.daily_insight == 'available':
            total['daily_available'] += 1
        elif account.daily_insight == 'claimed':
            total['daily_claimed'] += 1
        total['to_open'] += account.insights_to_open
        total['pending'] += account.pending_quests

        csv_data.append([address, account.insights.get('uncommon'), account.insights.get('rare'),
                         account.insights.get('legendary'), account.insights.get('mythical'),
                         account.daily_insight.capitalize(), account.insights_to_open,
                         account.pending_quests, account.next_breathe_time, account.exp, account.lvl])

    csv_data.append([])
    csv_data.append(['Total', total['uncommon'], total['rare'], total['legendary'], total['mythical'],
                     f'{total["daily_available"]}/{total["daily_claimed"]}',
                     total['to_open'], total['pending']])

    csv_data.append([])
    run_timestamp = str(datetime.now())
    csv_data.append(['Timestamp', run_timestamp])

    with open('results/stats.csv', 'w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, delimiter=',')
        writer.writerows(csv_data)

    with open('results/invites.txt', 'w', encoding='utf-8') as file:
        for ic in all_invite_codes:
            file.write(f'{ic}\n')

    logger.info('Stats are stored in results/stats.csv')
    logger.info('Invite codes are stored in results/invites.txt')
    logger.info(f'Timestamp: {run_timestamp}')


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
