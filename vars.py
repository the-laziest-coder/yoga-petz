import json


SITE_API_KEY = 'AIzaSyBPmETcQFfpDrw_eB6s8DCkDpYYBt3e8Wg'

SHARE_TWEET_FORMAT = '''Exciting news! 🧘
I'm sharing 5 exclusive invite codes to join well3.com 🎉
Join and bring your friends along. Stay Zen! Oohm! 🌱

{{invite_codes}}

@YogaPetz @keung $WELL 🏝️✨ '''

WALLET_SIGN_MESSAGE_FORMAT = '''Welcome to Yogapetz
Click "Sign" to continue.

Timestamp:
{{timestamp}}'''

BREATHE_SESSION_CONDITION = 'complete-breath-session'

INSIGHTS_CONTRACT_ADDRESS = '0x73A0469348BcD7AAF70D9E34BBFa794deF56081F'
INSIGHTS_CONTRACT_ABI = json.load(open('abi/insights.json'))

SCAN = 'https://opbnb.bscscan.com'

LOG_RESULT_TOPIC = '0x62437e973c89fadc3c7ae00cbd583c7225d842db5d4a5f003b322a8ab08b780a'
LOG_DATA_NAME_AND_COLOR = [('Uncommon', 'green'), ('Rare', 'cyan'),
                           ('Legendary', 'light_magenta'), ('Mythical', 'light_yellow')]
