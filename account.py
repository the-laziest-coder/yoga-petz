import random
import asyncio
import time
from loguru import logger
from datetime import timedelta
from typing import Union
from eth_account.messages import encode_defunct
from eth_account import Account as EthAccount
from web3.contract.async_contract import AsyncContractConstructor
from web3.exceptions import TransactionNotFound

from well3 import Well3
from twitter import Twitter
from models import AccountInfo
from config import MIN_INSIGHTS_TO_OPEN, FAKE_TWITTER
from vars import SHARE_TWEET_FORMAT, WALLET_SIGN_MESSAGE_FORMAT, BREATHE_SESSION_CONDITION, \
    INSIGHTS_CONTRACT_ADDRESS, INSIGHTS_CONTRACT_ABI, SCAN
from utils import wait_a_bit, get_w3, to_bytes, async_retry


class Account:

    def __init__(self, idx: Union[int, str], account: AccountInfo, well3: Well3, twitter: Twitter):
        self.idx = idx
        self.account = account
        self.well3 = well3
        self.twitter = twitter
        self.profile = None
        self.quests = None
        self.pending_quests = None

        self.w3 = get_w3(self.account.proxy)
        self.insights_contract = self.w3.eth.contract(INSIGHTS_CONTRACT_ADDRESS, abi=INSIGHTS_CONTRACT_ABI)
        self.private_key = None

    async def refresh_profile(self):
        self.profile = await self.well3.me()
        self.account.invite_codes = [rc['code'] for rc in self.profile['referralInfo']['myReferralCodes']
                                     if 'usedAt' not in rc]
        questing = self.profile['ygpzQuesting']
        self.quests = questing['info']
        self.pending_quests = questing['pendingVerify']
        self.account.exp = self.quests['exp']
        self.account.lvl = self.quests['rank']
        self.account.pending_quests = len(self.pending_quests)
        for _, task_info in self.quests['dailyProgress'].items():
            if task_info.get('condition') == BREATHE_SESSION_CONDITION:
                self.set_time_until_next_breathe(task_info)
                break

    def set_time_until_next_breathe(self, task_info):
        if task_info['expClaimed']:
            self.account.next_breathe_time = 'Completed'
            return
        if task_info['value'] == 0:
            self.account.next_breathe_time = 'Not started'
            return

        diff = task_info['nextAvailableFrom'] - int(time.time() * 1000)
        diff //= 1000

        self.account.next_breathe_time = 'in ' + str(timedelta(seconds=diff))

    async def check_invite_codes(self):
        await self.well3.generate_codes()
        await self.refresh_profile()

    async def do_quests(self):
        await self.do_quests_batch('dailyProgress')
        await self.do_quests_batch('specialProgress')

    async def do_quests_batch(self, batch_name: str):
        logger.info(f'{self.idx}) Starting {batch_name} tasks...')
        for task_id, task_info in self.quests[batch_name].items():
            task_title = task_info['title']
            if '</a>' in task_title:
                a_end = task_title.find('</a>')
                task_title = task_title[:a_end] + task_title[a_end + 4:]
                a_start_open = task_title.find('<a href=')
                a_start_close = task_title[a_start_open:].find('">') + a_start_open
                task_title = task_title[:a_start_open] + task_title[a_start_close + 2:]
            title = f"{task_title} [Exp: {task_info['exp']}]"
            if task_info['expClaimed']:
                continue
            if task_id in self.pending_quests:
                logger.info(f'{self.idx}) {title} in pending verify')
                continue
            logger.info(f'{self.idx}) {title}')
            done = await self.do_task(task_info)
            if done:
                await self.well3.claim_exp(task_id)
                logger.success(f'{self.idx}) Claimed exp or started verification')
            await wait_a_bit(random.uniform(3, 5))

    async def do_task(self, task_info: dict):
        if task_info.get('condition') == BREATHE_SESSION_CONDITION:

            already_done = task_info['value']
            needed = task_info['required']
            if already_done == needed:
                return False

            next_available_from = task_info['nextAvailableFrom']
            now = int(time.time() * 1000)
            if next_available_from is not None and next_available_from > now:
                td = timedelta(seconds=(next_available_from - now) // 1000)
                logger.info(f'{self.idx}) Next breathe available in {str(td)}')
                return False

            await self.well3.complete_breath_session()

            log_msg = f'{self.idx}) Breathe session done [{already_done + 1}/{needed}]'
            if already_done + 1 >= needed:
                logger.success(log_msg)
            else:
                logger.info(log_msg)
            return False

        special = task_info.get('special')
        if special is not None:

            match special['action']:
                case 'twitter-check-posted-media':
                    if FAKE_TWITTER:
                        return True
                    tweet_url = await self.post_tweet()
                    logger.info(f'{self.idx}) Tweet posted: {tweet_url}')
                case 'twitter-check-follow-profile':
                    if FAKE_TWITTER:
                        return True
                    follow_username = special['data']['url'].split('/')[-1]
                    await self.twitter.follow(follow_username)
                    logger.info(f'{self.idx}) {follow_username} followed')
                case 'twitter-check-retweet':
                    if FAKE_TWITTER:
                        return True
                    tweet_id = special['data']['rtRequiredTweetId']
                    await self.twitter.retweet(tweet_id)
                    await wait_a_bit()
                    liked = await self.twitter.like(tweet_id)
                    if not liked:
                        return False
                case 'twitter-check-profile-name':
                    logger.warning(f'{self.idx}) Changing profile name is not supported yet')
                    return False
                case 'twitter-check-profile-banner':
                    if FAKE_TWITTER:
                        return True
                    logger.warning(f'{self.idx}) Changing banner is not supported yet')
                    return False
                case unknown_action:
                    suffix = '. Trying to verify anyway' if FAKE_TWITTER else ''
                    logger.warning(f'{self.idx}) Unknown special action {unknown_action}{suffix}')
                    return FAKE_TWITTER

        return True

    async def post_tweet(self) -> str:
        parts = len(SHARE_TWEET_FORMAT.splitlines())
        last_exc = Exception("Can't send non-duplicate tweet")
        for ins_pos in random.sample([i for i in range(parts + 1)], parts + 1):
            tweet_text_parts = SHARE_TWEET_FORMAT.splitlines()
            tweet_text_parts.insert(ins_pos, '#Well')
            tweet_text = '\n'.join(tweet_text_parts)
            tweet_text = tweet_text.replace('{{invite_codes}}', '\n'.join(self.account.invite_codes))
            try:
                tweet_url = await self.twitter.post_tweet(tweet_text)
                return tweet_url
            except Exception as e:
                if 'Status is a duplicate' in str(e):
                    last_exc = e
                    continue
                raise
        raise last_exc

    async def link_wallet_if_needed(self, private_key):
        if self.profile['contractInfo'].get('linkedAddress') is None:
            timestamp = int(time.time() * 1000)
            message = WALLET_SIGN_MESSAGE_FORMAT.replace('{{timestamp}}', str(timestamp))
            signature = EthAccount().sign_message(encode_defunct(text=message), private_key).signature.hex()
            await wait_a_bit(2)
            await self.well3.link_wallet(message, signature)
            logger.success(f'{self.idx}) Wallet linked')
            await wait_a_bit(5)
            await self.refresh_profile()
        self.private_key = private_key

    @async_retry
    async def build_and_send_tx(self, func: AsyncContractConstructor):
        if self.private_key is None:
            raise Exception('No private key specified')
        tx = await func.build_transaction({
            'from': self.account.address,
            'nonce': await self.w3.eth.get_transaction_count(self.account.address),
            'gas': 300000,
            'gasPrice': 10008,
        })

        signed_tx = self.w3.eth.account.sign_transaction(tx, self.private_key)
        tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.rawTransaction)

        return tx_hash

    async def tx_verification(self, tx_hash, action, poll_latency=1):
        logger.info(f'{self.idx}) {action} - Tx sent')
        time_passed = 0
        tx_link = f'{SCAN}/tx/{tx_hash.hex()}'
        while time_passed < 150:
            try:
                tx_data = await self.w3.eth.get_transaction_receipt(tx_hash)
                if tx_data is not None:
                    if tx_data.get('status') == 1:
                        logger.success(f'{self.idx}) {action} - Successful tx: {tx_link}')
                    else:
                        logger.error(f'{self.idx}) {action} - Failed tx: {tx_link}')
                    return
            except TransactionNotFound:
                pass

            time_passed += poll_latency
            await asyncio.sleep(poll_latency)

        logger.warning(f'{self.idx}) {action} - Pending tx: {tx_link}')

    @async_retry
    async def check_daily_insight(self):
        daily_quest = self.profile['contractInfo']['dailyQuest']
        nonce = daily_quest['nonce']
        used = await self.insights_contract.functions.nonceUsed(nonce).call()
        self.account.daily_insight = 'claimed' if used else 'available'
        return self.account.daily_insight

    async def claim_daily_insight(self):
        logger.info(f'{self.idx}) Daily insight status: {await self.check_daily_insight()}')
        if self.account.daily_insight != 'available':
            return
        daily_quest = self.profile['contractInfo']['dailyQuest']
        nonce = daily_quest['nonce']
        signature = to_bytes(daily_quest['signature'])
        tx_hash = await self.build_and_send_tx(self.insights_contract.functions.nonceQuest(nonce, signature))
        await self.tx_verification(tx_hash, 'Claim daily insight')

    @async_retry
    async def check_rank_insights(self):
        rank_quest = self.profile['contractInfo']['rankupQuest']
        current_rank = rank_quest['currentRank']
        cnt = await self.insights_contract.functions.getQuests(current_rank, self.account.address).call()
        self.account.insights_to_open = cnt
        return self.account.insights_to_open

    async def claim_rank_insights(self):
        logger.info(f'{self.idx}) Rank insights available to open: {await self.check_rank_insights()}')
        if self.account.insights_to_open < MIN_INSIGHTS_TO_OPEN:
            return
        rank_quest = self.profile['contractInfo']['rankupQuest']
        current_rank = rank_quest['currentRank']
        signature = to_bytes(rank_quest['signature'])
        tx_hash = await self.build_and_send_tx(self.insights_contract.functions.
                                               rankupQuestAmount(current_rank, signature,
                                                                 self.account.insights_to_open))
        await self.tx_verification(tx_hash, 'Claim rank insight')

    @async_retry
    async def check_results(self):
        result = await self.insights_contract.functions.questResults(self.account.address).call()
        self.account.insights = {
            'uncommon': result[0],
            'rare': result[1],
            'legendary': result[2],
            'mythical': result[3],
        }

    async def check_insights(self):
        await self.check_daily_insight()
        await self.check_rank_insights()
        await self.check_results()
