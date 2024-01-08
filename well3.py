import aiohttp
import ua_generator
import time

from typing import Union

from models import AccountInfo
from twitter import Twitter
from utils import is_empty, handle_response, async_retry
from vars import SITE_API_KEY
from config import DISABLE_SSL


def _get_headers(info: AccountInfo) -> dict:
    if is_empty(info.user_agent):
        ua = ua_generator.generate(device='desktop', browser='chrome')
        info.user_agent = ua.text
        info.sec_ch_ua = f'"{ua.ch.brands[2:]}"'
        info.sec_ch_ua_platform = f'"{ua.platform.title()}"'
    return {
        'accept': '*/*',
        'accept-encoding': 'gzip, deflate, br',
        'accept-language': 'en-US,en;q=0.9',
        'origin': 'https://well3.com',
        'referer': f'https://well3.com/',
        'sec-ch-ua': info.sec_ch_ua,
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': info.sec_ch_ua_platform,
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'cross-site',
        'user-agent': info.user_agent,
    }


class Well3:
    AUTH_API_URL = 'https://well3.com/assets/__/auth/handler'
    API_URL = 'https://api.gm.io'

    GOOGLE_CREATE_AUTH_HEADERS = {
        'x-client-version': 'Chrome/Handler/2.20.2/FirebaseCore-web',
        # 'x-client-data': 'CJjeygE=',
    }
    GOOGLE_SIGN_IN_HEADERS = {
        'x-client-version': 'Chrome/JsCore/10.7.1/FirebaseCore-web',
        'x-firebase-gmpid': '1:322732006318:web:7d8d136900837cb38b463f',
        # 'x-client-data': 'CJjeygE=',
    }

    def __init__(self, idx: Union[int, str], account: AccountInfo, twitter: Twitter):
        self.idx = idx
        self.account = account
        self.twitter = twitter

        self.headers = _get_headers(self.account)

        self.oauth_access_token = None
        self.oauth_token_secret = None

        self.proxy = self.account.proxy
        if self.proxy and '|' in self.proxy:
            self.proxy = self.proxy.split('|')[0]
        self.proxy = None if is_empty(self.proxy) else self.proxy

    def get_conn(self):
        return None

    @async_retry
    async def request(self, method, url, acceptable_statuses=None, resp_handler=None, with_text=False, **kwargs):
        headers = self.headers.copy()
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))
        if DISABLE_SSL:
            kwargs.update({'ssl': False})
        async with aiohttp.ClientSession(connector=self.get_conn(), headers=headers) as sess:
            if method.lower() == 'get':
                async with sess.get(url, proxy=self.proxy, **kwargs) as resp:
                    return await handle_response(resp, acceptable_statuses, resp_handler, with_text)
            elif method.lower() == 'post':
                async with sess.post(url, proxy=self.proxy, **kwargs) as resp:
                    return await handle_response(resp, acceptable_statuses, resp_handler, with_text)
            else:
                raise Exception('Wrong request method')

    @async_retry
    async def sign_in_or_start_register_if_needed(self):
        if is_empty(self.account.well3_auth_token):
            await self.sign_in()
        else:
            if self.account.well3_auth_token_expire_at < int(time.time()) + 1800:
                await self.refresh_token()

        self.headers['authorization'] = self.account.well3_auth_token

        profile = await self.me()
        if profile['socialProfiles']['twitter'] is None:
            await self.link_twitter()
            profile = await self.me()

        return profile['referralInfo']['myReferrer']['userId'] is None

    async def sign_in(self):
        try:

            def _create_auth_handle(resp):
                _link = resp['authUri']
                _oauth_token = _link[_link.rfind('=') + 1:]
                _session_id = resp['sessionId']
                return _link, _oauth_token, _session_id

            link, oauth_token, session_id = await self.request(
                'POST',
                f'https://www.googleapis.com/identitytoolkit/v3/relyingparty/createAuthUri?key={SITE_API_KEY}',
                json={
                    'continueUri': self.AUTH_API_URL,
                    'customParameter': {},
                    'providerId': 'twitter.com',
                },
                headers={
                    'origin': 'https://well3.com',
                    'referer': f'https://well3.com/',
                    **self.GOOGLE_CREATE_AUTH_HEADERS,
                },
                acceptable_statuses=[200],
                resp_handler=_create_auth_handle
            )
        except Exception as e:
            raise Exception(f'Failed to get oauth link from google: {str(e)}')

        def _twitter_start_handler(resp_text):
            if f'href="{self.AUTH_API_URL}?state=' in resp_text \
                    and 'oauth_token' in resp_text \
                    and 'oauth_verifier' in resp_text:
                _state, _oauth_verifier = self._extract_state_and_oauth_verifier(resp_text)
                return _state, _oauth_verifier, None
            else:
                _authenticity_token = resp_text.split('<input name="authenticity_token" type="hidden" value="')[1]
                _authenticity_token = _authenticity_token[:_authenticity_token.find('">')]
                return None, None, _authenticity_token

        try:
            state, oauth_verifier, authenticity_token = await self.twitter.request(
                'GET', link, resp_handler=_twitter_start_handler, with_text=True
            )
        except Exception as e:
            raise Exception(f'Failed to get twitter authenticity token: {str(e)}')

        if state is None or oauth_verifier is None:
            try:
                state, oauth_verifier = await self.twitter.request(
                    'POST',
                    'https://api.twitter.com/oauth/authorize',
                    data={
                        'authenticity_token': authenticity_token,
                        'redirect_after_login': link,
                        'oauth_token': oauth_token,
                    },
                    headers={'content-type': 'application/x-www-form-urlencoded'},
                    resp_handler=self._extract_state_and_oauth_verifier,
                    with_text=True
                )
            except Exception as e:
                raise Exception(f'Failed to get twitter oauth verifier: {str(e)}')

        verify_link = f'{self.AUTH_API_URL}?state={state}&oauth_token={oauth_token}&oauth_verifier={oauth_verifier}'

        def _google_sign_in(resp):
            self.account.well3_auth_token = resp['idToken']
            self.account.well3_auth_token_expire_at = int(time.time()) + int(resp['expiresIn'])
            local_id = resp['localId']
            self.oauth_access_token = resp['oauthAccessToken']
            self.oauth_token_secret = resp['oauthTokenSecret']
            self.account.well3_refresh_token = resp['refreshToken']

        try:
            await self.request(
                'POST',
                f'https://identitytoolkit.googleapis.com/v1/accounts:signInWithIdp?key={SITE_API_KEY}',
                json={
                    'requestUri': verify_link,
                    'returnIdpCredential': True,
                    'returnSecureToken': True,
                    'sessionId': session_id,
                }, headers=self.GOOGLE_SIGN_IN_HEADERS, acceptable_statuses=[200], resp_handler=_google_sign_in)
        except Exception as e:
            raise Exception(f'Failed to sign in with verify link: {str(e)}')

    def _extract_state_and_oauth_verifier(self, content):
        link_part = content.split(f'href="{self.AUTH_API_URL}?state=')[1]
        state = link_part.split('&amp;')[0]
        oauth_verifier = link_part.split('oauth_verifier=')[1]
        oauth_verifier = oauth_verifier.split('">')[0]
        return state, oauth_verifier

    async def refresh_token(self):
        try:
            data = {
                'grant_type': 'refresh_token',
                'refresh_token': self.account.well3_refresh_token,
            }

            def _handler(resp):
                self.account.well3_auth_token = resp['id_token']
                self.account.well3_auth_token_expire_at = int(time.time()) + int(resp['expires_in'])
                self.account.well3_refresh_token = resp['refresh_token']

            await self.request('POST',
                               f'https://securetoken.googleapis.com/v1/token?key={SITE_API_KEY}', data=data,
                               headers=self.GOOGLE_SIGN_IN_HEADERS, acceptable_statuses=[200], resp_handler=_handler)

        except Exception as e:
            raise Exception(f'Failed to get account profile: {str(e)}')

    async def me(self):
        try:
            return await self.request('GET', f'{self.API_URL}/ygpz/me', acceptable_statuses=[200],
                                      resp_handler=lambda r: r)
        except Exception as e:
            raise Exception(f'Failed to get account profile: {str(e)}')

    async def link_twitter(self):
        try:
            return await self.request('POST', f'{self.API_URL}/ygpz/link-twitter', json={
                'oauth': {
                    'oauthAccessToken': self.oauth_access_token,
                    'oauthTokenSecret': self.oauth_token_secret,
                },
            }, acceptable_statuses=[200], resp_handler=lambda r: r)
        except Exception as e:
            raise Exception(f'Failed to link twitter: {str(e)}')

    async def use_invite_code(self, invite_code):
        try:
            return await self.request('POST', f'{self.API_URL}/ygpz/enter-referral-code', json={
                'code': invite_code,
            }, acceptable_statuses=[200], resp_handler=lambda r: r['generated'])
        except Exception as e:
            raise Exception(f'Failed to use enter invite code: {str(e)}')

    async def generate_codes(self):
        try:
            await self.request('POST', f'{self.API_URL}/ygpz/generate-codes', json={}, acceptable_statuses=[200])
        except Exception as e:
            raise Exception(f'Failed to generate invite code: {str(e)}')

    async def complete_breath_session(self):
        try:
            await self.request('POST', f'{self.API_URL}/ygpz/complete-breath-session', json={},
                               acceptable_statuses=[200])
        except Exception as e:
            raise Exception(f'Failed to complete breathe session: {str(e)}')

    async def claim_exp(self, task_id):
        try:
            await self.request('POST', f'{self.API_URL}/ygpz/claim-exp/{task_id}', json={}, acceptable_statuses=[200])
        except Exception as e:
            raise Exception(f'Failed to claim exp: {str(e)}')

    async def link_wallet(self, msg, signature):
        try:
            await self.request('POST', f'{self.API_URL}/ygpz/link-wallet', json={
                'address': self.account.address,
                'msg': msg,
                'signature': signature,
            }, acceptable_statuses=[200])
        except Exception as e:
            raise Exception(f'Failed to link wallet: {str(e)}')
