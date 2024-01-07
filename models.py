from dataclasses import dataclass, field
from dataclasses_json import dataclass_json
from typing import List


@dataclass_json
@dataclass
class AccountInfo:
    address: str = ''
    proxy: str = ''
    twitter_auth_token: str = ''
    well3_auth_token: str = ''
    well3_auth_token_expire_at: int = 0
    well3_refresh_token: str = ''
    user_agent: str = ''
    sec_ch_ua: str = ''
    sec_ch_ua_platform: str = ''
    exp: int = 0
    lvl: int = 0
    next_breathe_time: str = 'Not started'
    pending_quests: int = 0
    insights_to_open: int = 0
    daily_insight: str = 'unavailable'
    insights: dict[str, int] = field(default_factory=dict)
    invite_codes: List[str] = field(default_factory=list)

    def str_stats(self) -> str:
        insights_str = '\n'.join([f'\t\t{name}: {cnt}' for name, cnt in self.insights.items()])
        invites_str = ','.join(self.invite_codes)
        return f'\tExp: {self.exp}\n' \
               f'\tLvl: {self.lvl}\n' \
               f'\tPending quests: {self.pending_quests}\n' \
               f'\tNext breathe: {self.next_breathe_time}\n' \
               f'\tInsights to open: {self.insights_to_open}\n' \
               f'\tDaily insight: {self.daily_insight.capitalize()}\n' \
               f'\tInsights:\n{insights_str}\n' \
               f'\tInvite codes: {invites_str}'


@dataclass
class ProcessResult:
    invite_used: bool = False
