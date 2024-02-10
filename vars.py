import json


USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
SEC_CH_UA = '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
SEC_CH_UA_PLATFORM = '"macOS"'

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

LOG_RESULT_TOPIC = '0x00c995826b58cdd58dce644ee35d6a6db72c38615f9a3ed6184af4b3d7379540'
LOG_DATA_NAME_AND_COLOR = [('Uncommon', 'green'), ('Rare', 'cyan'),
                           ('Legendary', 'light_magenta'), ('Mythical', 'light_yellow')]

MINT_TAGS = [
    'walking', 'running', 'swimming', 'yoga', 'strength training', 'hiit', 'pilates',
    'cycling', 'dancing', 'hiking', 'biking', 'paddleboarding',
    'meditation', 'deep breathing', 'yoga', 'tai chi', 'journaling', 'gratitude practice',
    'healthy eating', 'plant-based diets', 'meal planning', 'meal prep', 'cooking classes', 'farmers markets',
    'hiking', 'camping', 'fishing', 'kayaking', 'rock climbing', 'bird watching', 'gardening', 'surfing',
    'fitness classes', 'team sports', 'running clubs', 'cycling clubs', 'dance classes',
    'massage', 'acupuncture', 'aromatherapy', 'spa treatments', 'relaxation techniques',
    'health seminars', 'workshops', 'webinars', 'podcasts', 'books',
    'community service', 'charity events', 'volunteering at animal shelter',
]
MINT_TAGS = list(set(MINT_TAGS))

MINT_CONTRACT_ADDRESS = '0x73A0469348BcD7AAF70D9E34BBFa794deF56081F'
