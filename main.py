import csv
import time
import random
import aiohttp
import asyncio

from termcolor import cprint
from loguru import logger
from datetime import datetime
from typing import Tuple, List, Optional
from eth_account import Account as EthAccount

from async_web3 import close_all_sessions
from storage import Storage
from models import AccountInfo, ProcessResult
from twitter import Twitter
from well3 import Well3
from account import Account
from config import DO_TASKS, CLAIM_DAILY_INSIGHT, CLAIM_RANK_INSIGHTS, \
    WAIT_BETWEEN_ACCOUNTS, THREADS_NUM, AUTO_UPDATE_INVITES, AUTO_UPDATE_INVITES_FROM_FIRST_COUNT, \
    SKIP_FIRST_ACCOUNTS, MOBILE_PROXY, RANDOM_ORDER, UPDATE_STORAGE_ACCOUNT_INFO, LOOP_RUNS, RANDOM_BATCH_CNT, \
    RING_COUNTRIES, WELL_ID_MODE, CLAIM_HUMAN_PROOF_MODE
from utils import wait_a_bit, async_retry, log_long_exc


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
                    await asyncio.sleep(WAIT_BETWEEN_ACCOUNTS[0] / THREADS_NUM)
                    return

                if type(AUTO_UPDATE_INVITES_FROM_FIRST_COUNT) is tuple:
                    from_addresses = list(enumerate(self.addresses[:AUTO_UPDATE_INVITES_FROM_FIRST_COUNT[1]], start=1))
                    random.shuffle(from_addresses)
                    max_use = AUTO_UPDATE_INVITES_FROM_FIRST_COUNT[0]
                else:
                    from_addresses = list(enumerate(self.addresses[:AUTO_UPDATE_INVITES_FROM_FIRST_COUNT], start=1))
                    max_use = None

                print()
                logger.info('Updating invites')

                for idx, address in from_addresses:
                    if max_use is not None and max_use <= 0:
                        break

                    if MOBILE_PROXY and idx != 1:
                        await asyncio.sleep(random.uniform(WAIT_BETWEEN_ACCOUNTS[0], WAIT_BETWEEN_ACCOUNTS[1]))

                    account_info = await refresh(f'Updating invites {idx}', address, self.storage)
                    if account_info is None:
                        continue

                    logger.info(f'Updating invites {idx}) Added new {len(account_info.invite_codes)}')
                    self.invites.extend(account_info.invite_codes)

                    if len(account_info.invite_codes) > 0 and max_use is not None:
                        max_use -= 1

                if type(AUTO_UPDATE_INVITES_FROM_FIRST_COUNT) is tuple:
                    random.shuffle(self.invites)

                logger.success(f'Invites updated: {len(self.invites)} new\n')

        except Exception as e:
            raise Exception(f'Update invites failed: {str(e)}')


@async_retry
async def change_ip(idx, link: str):
    async with aiohttp.ClientSession() as sess:
        async with sess.get(link) as resp:
            if resp.status != 200:
                raise Exception(f'Failed to change ip: Status = {resp.status}. Response = {await resp.text()}')
            logger.info(f'{idx}) Successfully changed ip: {await resp.text()}')


async def refresh(prefix: str, address: str, storage: Storage, check_insights: bool = False):
    logger.info(f'{prefix}) {address}')
    account_info = await storage.get_account_info(address)
    if account_info is None:
        return None
    if '|' in account_info.proxy:
        change_link = account_info.proxy.split('|')[1]
        await change_ip(prefix, change_link)
    twitter = Twitter(account_info)
    await twitter.start()
    well3 = Well3(prefix, account_info, twitter)
    if await well3.sign_in_or_start_register_if_needed():
        return None
    async with Account(prefix, account_info, well3, twitter) as account:
        await account.refresh_profile()
        if check_insights:
            await account.check_insights()
    await storage.set_account_info(address, account_info)
    return account_info


async def refresh_account(account_data, storage: Storage, _):
    idx, (wallet, proxy, twitter_token, _) = account_data
    address = EthAccount().from_key(wallet).address
    await refresh(f'Refreshing account {idx}', address, storage, check_insights=True)
    return ProcessResult()


claim_error_ids = []
claim_error_lock = asyncio.Lock()


async def process_account(account_data, storage: Storage, invites: InvitesHandler) \
        -> ProcessResult:
    result = ProcessResult()

    idx, (wallet, proxy, twitter_token, prompt, bybit) = account_data
    address = EthAccount().from_key(wallet).address
    logger.info(f'{idx}) Processing {address}')

    account_info = await storage.get_account_info(address)
    if account_info is None:
        logger.info(f'{idx}) Account info was not saved before')
        account_info = AccountInfo(address=address, proxy=proxy, twitter_auth_token=twitter_token)
    else:
        if UPDATE_STORAGE_ACCOUNT_INFO:
            account_info.proxy = proxy
            account_info.twitter_auth_token = twitter_token
        logger.info(f'{idx}) Saved account info restored')
    account_info.mint_prompt = prompt
    account_info.bybit_id = bybit

    if '|' in account_info.proxy:
        change_link = account_info.proxy.split('|')[1]
        await change_ip(idx, change_link)

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

    if CLAIM_HUMAN_PROOF_MODE:
        async with Account(idx, account_info, well3, twitter) as account:
            await account.refresh_profile()
            await account.link_wallet_if_needed(wallet)
            await account.claim_human_proof()
    elif WELL_ID_MODE:
        well_id_info = await well3.well_id()
        for _ in range(3):
            if well_id_info['mintInQueue'] or well_id_info.get('token') is None:
                logger.info(f'{idx}) Waiting for Well ID mint 10s more')
                await asyncio.sleep(10)
                well_id_info = await well3.well_id()
        if well_id_info['mintInQueue'] or well_id_info.get('token') is None:
            raise Exception(f'Minting Well ID takes too long')
        else:
            logger.info(f'{idx}) Well ID minted with name {well_id_info["token"].get("name")}')
            if well_id_info['linkedAddress'].lower() != account_info.address.lower():
                raise Exception(f'Well ID linked wallet differs from the current one: '
                                f'{well_id_info["linkedAddress"]}')
            account_info.well_id = True
            if well_id_info['country'] is None:
                country = random.choice(RING_COUNTRIES)
                logger.info(f'{idx}) Registering for Ring for country {country}')
                await well3.ring_register(country)
                well_id_info = await well3.well_id()
                if well_id_info['country'] != country:
                    logger.error(f'{idx}) Failed to register for Ring. Mismatched country')
                else:
                    logger.success(f'{idx}) Successfully registered for Ring')
            else:
                logger.info(f'{idx}) Ring registration was already done')
            account_info.ring_registered = well_id_info['country'] is not None
    else:
        async with Account(idx, account_info, well3, twitter) as account:

            await account.refresh_profile()

            logger.info(f'{idx}) Profile refreshed')

            await account.link_wallet_if_needed(wallet)

            if DO_TASKS:
                if await account.do_quests() > 0:
                    await account.refresh_profile()

            try:
                if CLAIM_DAILY_INSIGHT:
                    await wait_a_bit(5)
                    await account.claim_daily_insight()
                if CLAIM_RANK_INSIGHTS:
                    await wait_a_bit(5)
                    await account.claim_rank_insights()
            except Exception as e:
                wrong_linked_wallet = ''
                if account.profile["contractInfo"].get("linkedAddress").lower() != address.lower():
                    wrong_linked_wallet = f'Wrong linked wallet: {account.profile["contractInfo"].get("linkedAddress")}'
                elif 'execution reverted' in str(e):
                    wrong_linked_wallet = 'Probably rerun will help'
                await log_long_exc(idx, f'Claim error. {wrong_linked_wallet}', e)
                async with claim_error_lock:
                    claim_error_ids.append(idx)

            logger.info(f'{idx}) Checking insights')
            await account.check_insights()

    logger.info(f'{idx}) Account stats:\n{account_info.str_stats()}')

    await storage.set_account_info(address, account_info)

    await storage.async_save()

    return result


async def process_batch(bid: int, batch, storage: Storage, invites: InvitesHandler,
                        async_func, sleep):
    await asyncio.sleep(WAIT_BETWEEN_ACCOUNTS[0] / THREADS_NUM * bid)
    failed, used_invites = [], 0
    for idx, d in enumerate(batch):
        if sleep and idx != 0:
            await asyncio.sleep(random.uniform(WAIT_BETWEEN_ACCOUNTS[0], WAIT_BETWEEN_ACCOUNTS[1]))
        try:
            result = await async_func(d, storage, invites)
            if result.invite_used:
                used_invites += 1
        except Exception as e:
            failed.append(d)
            await log_long_exc(d[0], 'Process account error', e)

    return failed, used_invites


async def process(batches, storage: Storage, invites: InvitesHandler,
                  async_func, sleep=True):
    tasks = []
    for idx, b in enumerate(batches):
        tasks.append(asyncio.create_task(process_batch(idx, b, storage, invites, async_func, sleep)))
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
    with open('files/invites.txt', 'r', encoding='utf-8') as file:
        invites = file.read().splitlines()
        invites = [i.strip() for i in invites]
        invites = [i for i in invites if i != '']
    with open('files/prompts.txt', 'r', encoding='utf-8') as file:
        prompts = file.read().splitlines()
        prompts = [p.strip() for p in prompts]
    with open('files/bybits.txt', 'r', encoding='utf-8') as file:
        bybits = file.read().splitlines()
        bybits = [b.strip() for b in bybits]

    if len(prompts) < len(wallets):
        prompts.extend(['' for _ in range(len(wallets) - len(prompts))])
    if len(wallets) != len(proxies):
        logger.error('Proxies count does not match wallets count')
        return
    if len(wallets) != len(twitters):
        logger.error('Twitter count does not match wallets count')
        return
    if len(wallets) != len(prompts):
        logger.error('Prompts count does not match wallets count')
        return

    logger.info(f'Provided {len([b for b in bybits if b != ""])} Bybit accounts')
    if len(bybits) < len(wallets):
        bybits.extend(['' for _ in range(len(wallets) - len(bybits))])

    if WELL_ID_MODE:
        logger.error('Well ID registration closed. Use only Claim Human Proof Mode')
    elif not CLAIM_HUMAN_PROOF_MODE:
        logger.error('Farming campaign closed. Use only Claim Human Proof Mode')
        return

    storage = Storage('storage/data.json')
    storage.init()

    addresses = []
    for idx, w in enumerate(wallets, start=1):
        try:
            addresses.append(EthAccount().from_key(w).address)
        except Exception as e:
            raise Exception(f'Wrong private key #{idx}: {str(e)}')

    invites_handler = InvitesHandler(invites, storage, addresses)

    want_only = []

    def get_batches(skip: int = None, threads: int = THREADS_NUM):
        _data = list(enumerate(list(zip(wallets, proxies, twitters, prompts, bybits)), start=1))
        if skip is not None:
            _data = _data[skip:]
        if skip is not None and len(want_only) > 0:
            _data = [d for d in enumerate(list(zip(wallets, proxies, twitters, prompts, bybits)), start=1)
                     if d[0] in want_only]
        if RANDOM_ORDER or RANDOM_BATCH_CNT:
            random.shuffle(_data)
        if RANDOM_BATCH_CNT:
            _data = _data[:RANDOM_BATCH_CNT]
        _batches: List[List[Tuple[int, Tuple[str, str, str, str, str]]]] = [[] for _ in range(threads)]
        for _idx, d in enumerate(_data):
            _batches[_idx % threads].append(d)
        return _batches

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    results = loop.run_until_complete(process(
        get_batches(SKIP_FIRST_ACCOUNTS),
        storage, invites_handler, process_account
    ))

    failed = [r[0] for r in results]
    failed = [f[0] for fs in failed for f in fs]
    used_invites = [r[1] for r in results]

    used_invites = sum(used_invites)

    storage.save()

    print()
    logger.info('Finished')
    logger.info(f'Failed ids: {failed}')
    print()

    logger.info(f'Claim error: {[i for i in claim_error_ids]}')
    print()

    loop.run_until_complete(close_all_sessions())

    logger.info(f'Used invites: {used_invites}')

    csv_data = [['#', 'Address', 'Human Proof', 'Bybit ID', 'Well ID', 'Ring Registered',
                 'Total', 'Uncommon', 'Rare', 'Legendary', 'Mythical',
                 'Daily insight', 'Insights to open', 'Pending quests', 'Daily mint', 'Next breathe',
                 'Invite codes', 'Exp', 'Lvl']]
    total = {
        'total': 0,
        'uncommon': 0,
        'rare': 0,
        'legendary': 0,
        'mythical': 0,
        'daily_claimed': 0,
        'daily_available': 0,
        'daily_minted': 0,
        'to_open': 0,
        'pending': 0,
        'breathe': 0,
        'well_id': 0,
        'ring_registered': 0,
        'human_proof': 0,
        'bybit_id': 0,
    }
    all_invite_codes = []
    daily_available_acc_ids = []
    daily_mint_not_done_ids = []
    for idx, w in enumerate(wallets, start=1):
        address = EthAccount().from_key(w).address

        account = storage.get_final_account_info(address)
        if account is None:
            csv_data.append([idx, address])
            continue

        all_invite_codes.extend(account.invite_codes)

        acc_total = account.insights.get('uncommon', 0) + account.insights.get('rare', 0) + \
            account.insights.get('legendary', 0) + account.insights.get('mythical', 0)

        total['total'] += acc_total
        total['uncommon'] += account.insights.get('uncommon', 0)
        total['rare'] += account.insights.get('rare', 0)
        total['legendary'] += account.insights.get('legendary', 0)
        total['mythical'] += account.insights.get('mythical', 0)
        if account.daily_insight.endswith('available'):
            total['daily_available'] += 1
            daily_available_acc_ids.append(idx)
        elif account.daily_insight.endswith('claimed'):
            total['daily_claimed'] += 1
        if account.daily_mint:
            total['daily_minted'] += 1
        else:
            daily_mint_not_done_ids.append(idx)
        total['to_open'] += account.insights_to_open
        total['pending'] += account.pending_quests
        if account.next_breathe_str() == 'Completed':
            total['breathe'] += 1
        total['well_id'] += 1 if account.well_id else 0
        total['ring_registered'] += 1 if account.ring_registered else 0
        total['human_proof'] += 1 if account.claimed_human_proof else 0
        total['bybit_id'] += 1 if account.bybit_id != '' else 0

        csv_data.append([idx, address, account.claimed_human_proof, account.bybit_id,
                         account.well_id, account.ring_registered, acc_total,
                         account.insights.get('uncommon'), account.insights.get('rare'),
                         account.insights.get('legendary'), account.insights.get('mythical'),
                         account.daily_insight.capitalize(), account.insights_to_open,
                         account.pending_quests, account.daily_mint, account.next_breathe_str(),
                         len(account.invite_codes), account.exp, account.lvl])

    csv_data.extend([[], ['', 'Total', total['human_proof'], total['bybit_id'],
                          total['well_id'], total['ring_registered'], total['total'],
                          total['uncommon'], total['rare'],
                          total['legendary'], total['mythical'],
                          f'{total["daily_available"]}/{total["daily_claimed"]}',
                          total['to_open'], total['pending'], total['daily_minted'], total['breathe']]])
    csv_data.append(['', '', 'Human Proof', 'Bybit ID', 'Well ID', 'Ring Registered',
                     'Total', 'Uncommon', 'Rare', 'Legendary', 'Mythical',
                     'Daily insight', 'Insights to open', 'Pending quests', 'Daily mint', 'Next breathe'])

    run_timestamp = str(datetime.now())
    csv_data.extend([[], ['', 'Timestamp', run_timestamp]])

    with open('results/stats.csv', 'w', encoding='utf-8', newline='') as file:
        writer = csv.writer(file, delimiter=';')
        writer.writerows(csv_data)

    with open('results/invites.txt', 'w', encoding='utf-8') as file:
        for ic in all_invite_codes:
            file.write(f'{ic}\n')

    daily_available_acc_ids = [i for i in daily_available_acc_ids]
    daily_mint_not_done_ids = [i for i in daily_mint_not_done_ids]

    if not WELL_ID_MODE:
        logger.info(f'Daily available accounts: {daily_available_acc_ids}\n')
        logger.info(f'Daily mint not done accounts: {daily_mint_not_done_ids}\n')
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

    if LOOP_RUNS:
        while True:
            st = int(time.time())
            main()
            time.sleep(3600 * 3)
            time.sleep(random.randint(1, 20) * 60)
            main()
            time.sleep(3600 * 24 - (int(time.time()) - st))
            time.sleep(random.randint(0, 120))
    else:
        main()
