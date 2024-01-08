import random
import brotli
import json
import binascii
import ua_generator
import aiohttp

from models import AccountInfo
from utils import is_empty, handle_response, async_retry
from config import DISABLE_SSL


def generate_csrf_token(size=16):
    data = random.getrandbits(size * 8).to_bytes(size, "big")
    return binascii.hexlify(data).decode()


def _get_headers(info: AccountInfo) -> dict:
    if is_empty(info.user_agent):
        ua = ua_generator.generate(device='desktop', browser='chrome')
        info.user_agent = ua.text
        info.sec_ch_ua = f'"{ua.ch.brands[2:]}"'
        info.sec_ch_ua_platform = f'"{ua.platform.title()}"'
    return {
        'accept': '*/*',
        'accept-language': 'en;q=0.9',
        'authorization': 'Bearer AAAAAAAAAAAAAAAAAAAAANRILgAAAAAAnNwIzUejRCOuH5E6I8xnZz4puTs%3D1Zv7ttfk8LF81IUq16cHjhLTvJu4FA33AGWWjCpTnA',
        'content-type': 'application/json',
        'origin': 'https://mobile.twitter.com',
        'referer': 'https://mobile.twitter.com/',
        'sec-ch-ua': info.sec_ch_ua,
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': info.sec_ch_ua_platform,
        'sec-fetch-dest': 'empty',
        'sec-fetch-mode': 'cors',
        'sec-fetch-site': 'same-origin',
        'x-twitter-active-user': 'yes',
        'x-twitter-auth-type': 'OAuth2Session',
        'x-twitter-client-language': 'en',
        'x-csrf-token': '',
        'user-agent': info.user_agent,
    }


class Twitter:

    def __init__(self, account_info: AccountInfo):
        self.cookies = {
            'auth_token': account_info.twitter_auth_token,
            'ct0': '',
        }
        self.headers = _get_headers(account_info)
        self.proxy = account_info.proxy
        if self.proxy and '|' in self.proxy:
            self.proxy = self.proxy.split('|')[0]
        self.proxy = None if is_empty(self.proxy) else self.proxy

    async def start(self):
        ct0 = await self._get_ct0()
        self.cookies.update({'ct0': ct0})
        self.headers.update({'x-csrf-token': ct0})

    def get_conn(self):
        return None

    def set_cookies(self, resp_cookies):
        self.cookies.update({name: value.value for name, value in resp_cookies.items()})

    @async_retry
    async def request(self, method, url, acceptable_statuses=None, resp_handler=None, with_text=False, **kwargs):
        headers = self.headers.copy()
        cookies = self.cookies.copy()
        if 'headers' in kwargs:
            headers.update(kwargs.pop('headers'))
        if 'cookies' in kwargs:
            cookies.update(kwargs.pop('cookies'))
        if DISABLE_SSL:
            kwargs.update({'ssl': False})
        async with aiohttp.ClientSession(connector=self.get_conn(), headers=headers, cookies=cookies) as sess:
            if method.lower() == 'get':
                async with sess.get(url, proxy=self.proxy, **kwargs) as resp:
                    self.set_cookies(resp.cookies)
                    return await handle_response(resp, acceptable_statuses, resp_handler, with_text)
            elif method.lower() == 'post':
                async with sess.post(url, proxy=self.proxy, **kwargs) as resp:
                    self.set_cookies(resp.cookies)
                    return await handle_response(resp, acceptable_statuses, resp_handler, with_text)
            else:
                raise Exception('Wrong request method')

    async def _get_ct0(self):
        try:
            kwargs = {'ssl': False} if DISABLE_SSL else {}
            async with aiohttp.ClientSession(connector=self.get_conn(),
                                             headers=self.headers, cookies=self.cookies) as sess:
                async with sess.get('https://twitter.com/i/api/1.1/dm/user_updates.json?',
                                    proxy=self.proxy, **kwargs) as resp:
                    new_csrf = resp.cookies.get("ct0")
                    if new_csrf is None:
                        raise Exception('Empty new csrf')
                    new_csrf = new_csrf.value
                    return new_csrf
        except Exception as e:
            raise Exception(f'Failed get ct0: {str(e)}')

    async def get_my_username(self):
        url = 'https://api.twitter.com/1.1/account/settings.json'
        params = {
            'include_mention_filter': 'true',
            'include_nsfw_user_flag': 'true',
            'include_nsfw_admin_flag': 'true',
            'include_ranked_timeline': 'true',
            'include_alt_text_compose': 'true',
            'ext': 'ssoConnections',
            'include_country_code': 'true',
            'include_ext_dm_nsfw_media_filter': 'true',
            'include_ext_sharing_audiospaces_listening_data_with_followers': 'true',
        }
        try:
            return await self.request("GET", url, params=params, resp_handler=lambda r: r['screen_name'].lower())
        except Exception as e:
            raise Exception(f'Get my username error: {str(e)}')

    async def get_followers_count(self, username):
        url = 'https://twitter.com/i/api/graphql/G3KGOASz96M-Qu0nwmGXNg/UserByScreenName'
        params = {
            'variables': to_json({"screen_name": username, "withSafetyModeUserFields": True}),
            'features': to_json({
                "hidden_profile_likes_enabled": True,
                "hidden_profile_subscriptions_enabled": True,
                "responsive_web_graphql_exclude_directive_enabled": True,
                "verified_phone_label_enabled": False,
                "subscriptions_verification_info_is_identity_verified_enabled": True,
                "subscriptions_verification_info_verified_since_enabled": True,
                "highlights_tweets_tab_ui_enabled": True,
                "creator_subscriptions_tweet_preview_api_enabled": True,
                "responsive_web_graphql_skip_user_profile_image_extensions_enabled": False,
                "responsive_web_graphql_timeline_navigation_enabled": True
            }),
            'fieldToggles': to_json({"withAuxiliaryUserLabels": False})
        }
        try:
            return await self.request(
                "GET", url, params=params,
                resp_handler=lambda r: r['data']['user']['result']['legacy']['followers_count']
            )
        except Exception as e:
            raise Exception(f'Get followers count error: {str(e)}')

    async def get_user_id(self, username):
        url = 'https://twitter.com/i/api/graphql/9zwVLJ48lmVUk8u_Gh9DmA/ProfileSpotlightsQuery'
        if username[0] == '@':
            username = username[1:]
        username = username.lower()
        params = {
            'variables': to_json({'screen_name': username})
        }
        try:
            return await self.request(
                "GET", url, params=params,
                resp_handler=lambda r: int(r['data']['user_result_by_screen_name']['result']['rest_id'])
            )
        except Exception as e:
            raise Exception(f'Get user id error: {str(e)}')

    async def follow(self, username):
        user_id = await self.get_user_id(username)
        url = 'https://twitter.com/i/api/1.1/friendships/create.json'
        params = {
            'include_profile_interstitial_type': '1',
            'include_blocking': '1',
            'include_blocked_by': '1',
            'include_followed_by': '1',
            'include_want_retweets': '1',
            'include_mute_edge': '1',
            'include_can_dm': '1',
            'include_can_media_tag': '1',
            'include_ext_has_nft_avatar': '1',
            'include_ext_is_blue_verified': '1',
            'include_ext_verified_type': '1',
            'include_ext_profile_image_shape': '1',
            'skip_status': '1',
            'user_id': user_id,
        }
        headers = {
            'content-type': 'application/x-www-form-urlencoded'
        }
        try:
            await self.request('POST', url, params=params, headers=headers, resp_handler=lambda r: r['id'])
        except Exception as e:
            raise Exception(f'Follow error: {str(e)}')

    async def post_tweet(self, text, tweet_id=None) -> str:
        action = "CreateTweet"
        query_id = "GUFG748vuvmewdXbB5uPKg"
        _json = dict(
            variables=dict(
                tweet_text=text,
                media=dict(
                    media_entities=[],
                    possibly_sensitive=False
                ),
                semantic_annotation_ids=[],
                dark_request=False
            ),
            features=dict(
                freedom_of_speech_not_reach_fetch_enabled=True,
                graphql_is_translatable_rweb_tweet_is_translatable_enabled=True,
                longform_notetweets_consumption_enabled=True,
                longform_notetweets_inline_media_enabled=True,
                longform_notetweets_rich_text_read_enabled=True,
                responsive_web_edit_tweet_api_enabled=True,
                responsive_web_enhance_cards_enabled=False,
                responsive_web_graphql_exclude_directive_enabled=True,
                responsive_web_graphql_skip_user_profile_image_extensions_enabled=False,
                responsive_web_graphql_timeline_navigation_enabled=True,
                standardized_nudges_misinfo=True,
                tweet_awards_web_tipping_enabled=False,
                tweet_with_visibility_results_prefer_gql_limited_actions_policy_enabled=False,
                tweetypie_unmention_optimization_enabled=True,
                verified_phone_label_enabled=False,
                view_counts_everywhere_api_enabled=True
            ),
            queryId=query_id)

        if tweet_id:
            _json['variables']['reply'] = dict(
                in_reply_to_tweet_id=tweet_id,
                exclude_reply_user_ids=[]
            )

        url = f'https://twitter.com/i/api/graphql/{query_id}/{action}'

        def _handler(resp):
            _result = resp['data']['create_tweet']['tweet_results']['result']
            _username = _result['core']['user_results']['result']['legacy']['screen_name']
            _tweet_id = _result['rest_id']
            _url = f'https://twitter.com/{_username}/status/{_tweet_id}'
            return _url

        try:
            return await self.request('POST', url, json=_json, resp_handler=_handler)
        except Exception as e:
            raise Exception(f'Post tweet error: {str(e)}')

    async def retweet(self, tweet_id):
        action = 'CreateRetweet'
        query_id = 'ojPdsZsimiJrUGLR1sjUtA'
        url = f'https://twitter.com/i/api/graphql/{query_id}/{action}'
        _json = {
            'variables': {
                'tweet_id': tweet_id,
                'dark_request': False
            },
            'queryId': query_id
        }
        try:
            return await self.request('POST', url, json=_json, resp_handler=lambda r: r)
        except Exception as e:
            raise Exception(f'Retweet error: {str(e)}')

    async def like(self, tweet_id) -> bool:
        action = 'FavoriteTweet'
        query_id = 'lI07N6Otwv1PhnEgXILM7A'
        url = f'https://twitter.com/i/api/graphql/{query_id}/{action}'
        _json = {
            'variables': {
                'tweet_id': tweet_id,
                'dark_request': False
            },
            'queryId': query_id
        }
        try:
            return await self.request(
                'POST', url, json=_json,
                resp_handler=lambda r: r['data']['favorite_tweet'] == 'Done'
            )
        except Exception as e:
            raise Exception(f'Like error: {str(e)}')


def to_json(obj):
    return json.dumps(obj, separators=(',', ':'), ensure_ascii=True)
