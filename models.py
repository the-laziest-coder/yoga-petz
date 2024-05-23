import time
from termcolor import colored
from datetime import timedelta
from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from typing import List, Union


@dataclass_json
@dataclass
class AccountInfo:
    address: str = ''
    proxy: str = ''
    twitter_auth_token: str = ''
    well3_auth_token: str = ''
    well3_auth_token_expire_at: int = 0
    well3_refresh_token: str = ''
    cf_clearance: str = ''
    user_agent: str = ''
    sec_ch_ua: str = ''
    sec_ch_ua_platform: str = ''
    exp: int = 0
    lvl: int = 0
    next_breathe_time: Union[int, str] = 'Not started'
    pending_quests: int = 0
    insights_to_open: int = 0
    daily_insight: str = 'unavailable'
    daily_mint: bool = False
    insights: dict[str, int] = field(default_factory=dict)
    invite_codes: List[str] = field(default_factory=list)
    mint_prompt: str = ''
    well_id: bool = False
    ring_registered: bool = False

    def next_breathe_str(self) -> str:
        if type(self.next_breathe_time) is str:
            return self.next_breathe_time
        diff = self.next_breathe_time - int(time.time() * 1000)
        diff //= 1000
        if diff <= 0:
            return 'Available'
        return 'in ' + str(timedelta(seconds=diff))

    def str_stats(self) -> str:
        insights_str = '\n'.join([f'\t\t{name}: {cnt}' for name, cnt in self.insights.items()])
        invites_str = ','.join(self.invite_codes)
        return f'\tExp: {self.exp}\n' \
               f'\tLvl: {self.lvl}\n' \
               f'\tPending quests: {self.pending_quests}\n' \
               f'\tNext breathe: {self.next_breathe_str()}\n' \
               f'\tDaily mint: {self.daily_mint}\n' \
               f'\tInsights to open: {self.insights_to_open}\n' \
               f'\tDaily insight: {self.daily_insight_colored}\n' \
               f'\tInsights:\n{insights_str}\n' \
               f'\tInvite codes: {invites_str}\n' \
               f'\tWell ID: {self.well_id}\n' \
               f'\tRing Registered: {self.ring_registered}\n'

    @property
    def daily_insight_colored(self):
        daily_insight = self.daily_insight
        if 'SUPER' in daily_insight:
            return colored('SUPER ', 'light_yellow') + ' '.join(daily_insight.split(' ')[1:])
        return daily_insight


@dataclass
class ProcessResult:
    invite_used: bool = False
