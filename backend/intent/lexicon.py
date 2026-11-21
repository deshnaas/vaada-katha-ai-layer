"""Word lists used by the intent extractor (Hindi / English / Hinglish).

Everything here is plain data so a teammate can add words without touching logic.
All Latin words are lowercase; Devanagari spellings are included where common.
Speech-to-text may spell the same Hindi word several ways, so we list variants.
"""

# ---------------------------------------------------------------- numbers
NUMBER_WORDS = {
    # Hindi 0-99 (with common spelling variants)
    "zero": 0, "shunya": 0,
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "chhah": 6, "che": 6, "chhey": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
    "gyarah": 11, "barah": 12, "terah": 13, "chaudah": 14, "pandrah": 15, "solah": 16,
    "satrah": 17, "atharah": 18, "unnis": 19, "unees": 19, "bees": 20, "ikkis": 21,
    "baais": 22, "teis": 23, "chaubis": 24, "pachchis": 25, "pachis": 25, "chhabbis": 26,
    "sattais": 27, "atthais": 28, "untis": 29, "tees": 30, "iktis": 31, "battis": 32,
    "tentis": 33, "chauntis": 34, "paintis": 35, "chhattis": 36, "saintis": 37,
    "adtis": 38, "untalis": 39, "chalis": 40, "chaalis": 40, "chaliis": 40,
    "iktalis": 41, "bayalis": 42, "taintalis": 43, "chauvalis": 44, "paintalis": 45,
    "chhiyalis": 46, "saintalis": 47, "adtalis": 48, "unchas": 49, "pachas": 50,
    "ikyavan": 51, "baavan": 52, "tirpan": 53, "chauvan": 54, "pachpan": 55,
    "chhappan": 56, "sattavan": 57, "atthavan": 58, "unsath": 59, "saath": 60, "sath": 60,
    "iksath": 61, "baasath": 62, "tirsath": 63, "chausath": 64, "painsath": 65,
    "chhiyasath": 66, "sadsath": 67, "adsath": 68, "unhattar": 69, "sattar": 70,
    "ikhattar": 71, "bahattar": 72, "tihattar": 73, "chauhattar": 74, "pachhattar": 75,
    "chhihattar": 76, "satattar": 77, "athattar": 78, "unasi": 79, "assi": 80,
    "ikyasi": 81, "bayasi": 82, "tirasi": 83, "chaurasi": 84, "pachasi": 85,
    "chhiyasi": 86, "sattasi": 87, "atthasi": 88, "nawasi": 89, "nabbe": 90,
    "ikyanve": 91, "baanve": 92, "tiranve": 93, "chauranve": 94, "pachanve": 95,
    "chhiyanve": 96, "sattanve": 97, "atthanve": 98, "ninyanve": 99,
    # English
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
    "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50,
    "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
MULT = {
    "sau": 100, "सौ": 100, "hundred": 100,
    "hazaar": 1000, "hazar": 1000, "hajar": 1000, "hajaar": 1000, "हज़ार": 1000,
    "हजार": 1000, "thousand": 1000,
    "lakh": 100000, "lac": 100000, "lakhs": 100000, "लाख": 100000,
}
# "sawa sau" = 125, "dedh sau" = 150, "dhai hazaar" = 2500
FRAC_PREFIX = {"sawa": 1.25, "dedh": 1.5, "derh": 1.5, "dhai": 2.5, "dhaai": 2.5}
# "saade teen sau" = 350, "paune do sau" = 175
SAADE = {"saade", "sade"}
PAUNE = {"paune"}
# Words that are ALSO ordinary words ("do" = give, "ek" = a/one, "saath" = with).
# They only count as numbers when followed by a multiplier or a currency word.
NUMBER_HOMOGRAPHS = {"ek", "do", "saath", "sath", "one", "che"}

CURRENCY_WORDS = {"rupaye", "rupaiye", "rupay", "rupee", "rupees", "rupiya", "rupye",
                  "rs", "inr", "रुपये", "रुपए", "रूपये"}
# A number followed by one of these is NOT an amount (2 kilo, 15 tarikh, 5 baje...)
UNIT_WORDS = {
    "kilo", "kg", "kgs", "gram", "grams", "gm", "packet", "packets", "pkt", "litre",
    "liter", "ltr", "dozen", "darjan", "piece", "pieces", "pcs", "peti", "bori", "bag",
    "bottle", "botal", "units", "item", "items", "log", "baar", "din", "days", "day",
    "tarikh", "tareekh", "date", "तारीख", "baje", "bajke", "mahine", "mahina", "month",
    "hafte", "hafta", "week", "saal", "year", "ghante", "ghanta", "hour", "hours",
    "minute", "minutes", "percent", "number",
    "th", "st", "nd", "rd", "pm", "am",        # 15th, 1st, 5pm
}

# ---------------------------------------------------------------- particles
PARTICLES = {"ka", "ki", "ke", "ne", "ko", "se", "का", "की", "के", "ने", "को", "से"}
NE_MARKERS = {"ne", "ने", "usne", "isne", "unhone", "unne"}
KO_MARKERS = {"ko", "को", "usko", "isko", "unko"}
PRONOUNS = {"usne", "isne", "unhone", "usko", "isko", "unko", "uska", "iska", "unka",
            "uski", "iski", "unki", "usse", "isse", "he", "she", "they", "him", "her",
            "his", "their"}
TITLES = {"bhai", "bhaiya", "bhaiyya", "ji", "sahab", "sahib", "uncle", "aunty", "auntie",
          "didi", "madam", "sir", "mam", "chacha", "chachi", "mama", "bhabhi"}
WALE = {"wale", "wala", "wali", "vale", "vala", "वाले", "वाला", "वाली"}

# ---------------------------------------------------------------- transaction words
CREDIT_STRONG = {"udhaar", "udhar", "udhari", "udhaari", "udhaaree", "उधार", "उधारी",
                 "credit"}
# "baaki" alone is often a STATEMENT ("300 baaki hai" = 300 is pending), so it only
# counts as credit when paired with a record verb ("300 baaki likho").
BAAKI = {"baaki", "baki", "bakaya", "बाकी"}
RECORD_VERBS = {"likho", "likh", "likhna", "likhdo", "likhdena", "likhiye", "add", "note",
                "enter", "record", "mark", "dalo", "daalo", "daal"}
FOREIGN_CURRENCY = {"dollar", "dollars", "usd", "euro", "euros", "pound", "pounds", "dirham",
                    "dirhams", "yen", "cents"}
CREDIT_PHRASES = [" khate mein ", " khata mein ", " khate me ", " khata me ", " le gaya ",
                  " le gayi ", " le gaye ", " lekar gaya ", " lekar gayi ", " ghar le gaya "]
CREDIT_MED = {"liya", "liye", "लिया", "लिए"}      # counted only with "ne"
DIYE = {"diya", "diye", "dia", "दिया", "दिए", "dediya", "dediye"}
PAYMENT_STRONG = {"wapas", "vapas", "वापस", "chuka", "chukaya", "chukaye", "chukta",
                  "jama", "जमा", "paid", "pay", "payment", "received", "receive", "settle",
                  "settled", "cleared", "repaid", "bhugtan", "भुगतान", "lauta", "lautaya"}
REPAY_VERBS = {"wapas", "vapas", "वापस", "chuka", "chukaya", "chukaye", "chukta", "repaid",
               "settle", "settled", "cleared", "lautaya", "lauta"}
PAYMENT_MED = {"mila", "mile", "mil", "aaya", "aaye", "collected", "collection"}
TAKEN_FROM = {"liya", "liye", "mile", "mila", "mil", "aaya", "aaye"}  # with "se" = money received
PAYMENT_PHRASES = [" bhar diya ", " bhar diye ", " pay kiya ", " pay kar diya "]
PAYMENT_METHODS = {
    "UPI": {"upi", "phonepe", "phonepay", "gpay", "googlepay", "paytm", "bhim", "qr",
            "online", "ऑनलाइन"},
    "CASH": {"cash", "naqad", "nakad", "कैश"},
}
METHOD_WORDS = set().union(*PAYMENT_METHODS.values())

# ---------------------------------------------------------------- queries
QUESTION_WORDS = {"kitna", "kitne", "kitni", "kya", "kaun", "kon", "kaunsa", "kisne",
                  "kisko", "kis", "kab", "how", "what", "who", "when", "which",
                  "कितना", "कितने", "कौन", "किसने", "कब", "क्या"}
BALANCE_WORDS = {"total", "balance", "baaki", "baki", "bacha", "bachha", "bakaya",
                 "outstanding", "due", "hisaab", "hisab", "hissab", "टोटल", "बाकी", "हिसाब",
                 "owe", "owes", "pending"}
HISTORY_WORDS = {"history", "itihas", "transactions", "transaction", "entries", "entry",
                 "statement", "lenden", "pichla", "pichli", "pichle", "last", "recent",
                 "details", "detail", "list", "kab", "dikhao", "show"}
SUMMARY_WORDS = {"summary", "bikri", "sales", "sale", "collection", "hisaab", "hisab",
                 "total", "kitna", "kitne", "kitni", "how", "सारांश"}
CREDIT_WORDS_FOR_SUMMARY = {"udhaar", "udhar", "udhari", "udhaari", "उधार", "credit", "gaya", "gayi", "diya", "diye"}
PAYMENT_WORDS_FOR_SUMMARY = {"payment", "paid", "jama", "received", "aaya", "aaye", "mila",
                             "mile", "collection", "collected", "वापस", "wapas", "भुगतान"}

# ---------------------------------------------------------------- commitments (promises)
FUTURE_VERBS = {"dega", "degi", "denge", "dunga", "dungi", "देगा", "देगी", "देंगे",
                "karega", "karegi", "karenge", "bhejega", "bhejegi", "bhejenge",
                "aayega", "aayegi", "aayenge", "promise", "promised", "wada", "vaada", "vada",
                "wadaa", "वादा", "will", "dene", "dena"}
# these alone are not enough: need a pay/give idea next to them
PAY_IDEA = {"pay", "payment", "paisa", "paise", "give", "dega", "degi", "denge", "dene",
            "dena", "de", "wapas", "bhejega", "bhejegi", "bhejenge", "aayega", "aayegi",
            "aayenge", "rupaye", "rs", "udhaar", "udhar"}
FUTURE_CUES = {"dega", "degi", "denge", "dunga", "dungi", "देगा", "देगी", "देंगे", "karega",
               "karegi", "karenge", "hoga", "hogi", "honge", "will", "aayega", "aayegi",
               "aayenge", "bhejega", "bhejegi", "bhejenge", "dena", "dene", "promise", "vaada",
               "wada", "vada", "वादा"}
PAST_CUES = {"tha", "thi", "the", "diya", "diye", "gaya", "gayi", "gaye", "liya", "liye",
             "kiya", "kiye", "mila", "mile", "aaya", "aaye", "ago", "was", "gave", "paid",
             "took", "received", "थे", "था", "थी", "दिया", "दिए"}
PROMISE_PAST = [" bola tha ", " bola hai ", " kaha tha ", " kaha hai ", " promise kiya ",
                " wada kiya ", " vaada kiya ", " vada kiya ", " promised "]

# ---------------------------------------------------------------- control utterances
CORR_START = {"nahi", "nahin", "nai", "nahee", "no", "nope", "galat", "wrong", "sorry",
              "matlab", "actually", "नहीं", "नही", "गलत", "मतलब"}
CORR_LEAD_FILLERS = {"arre", "are", "arey", "oh", "ohh", "uh", "um", "hmm", "wait", "aur", "oops"}
CONFIRM_TOKENS = {"haan", "han", "ha", "haa", "hanji", "haanji", "ji", "yes", "yeah", "yep",
                  "ok", "okay", "theek", "thik", "sahi", "correct", "right", "done", "kar",
                  "do", "karo", "kardo", "bilkul", "pakka", "confirm", "save", "likh", "likho",
                  "hai", "haan", "हाँ", "हां", "ठीक", "सही", "जी", "kijiye", "please", "plz"}
CANCEL_PHRASES = [" rehne do ", " rahne do ", " rehne dijiye ", " chhod do ", " chod do ",
                  " cancel ", " mat likho ", " mat karo ", " ruko ", " stop ", " forget it ",
                  " never mind ", " nevermind ", " jaane do ", " jane do ", " koi baat nahi ",
                  " रहने दो ", " छोड़ दो ", " mat likh "]

# ---------------------------------------------------------------- misc
DAYS_ABOUT_TODAY = {"aaj", "today", "आज", "aj"}
FILLERS = {
    "hey", "hi", "hello", "vaada", "vada", "wada", "namaste", "please", "plz", "zara",
    "hai", "hain", "tha", "thi", "the", "ho", "hoga", "hogi", "kar", "karo", "karna",
    "kare", "kijiye", "kardo", "do", "de", "dena", "dene", "deta", "dedo", "dedena",
    "likho", "likh", "likhna", "likhdo", "likhdena", "likhiye", "add", "note", "enter",
    "record", "mark", "save", "aur", "and", "also", "bhi", "to", "toh", "a", "an", "is",
    "are", "of", "on", "in", "for", "from", "with", "mein", "me", "mai", "main", "mera",
    "meri", "mere", "mujhe", "hum", "aap", "tum", "yeh", "ye", "wo", "woh", "us", "isko",
    "par", "pe", "tak", "kya", "kyun", "kuch", "sab", "ab", "abhi", "phir", "fir", "bas",
    "sirf", "hi", "na", "kaha", "bola", "diya", "diye", "paise", "paisa", "rupaye", "dikhao",
    "bataiye", "batao", "bata", "bolo", "suno", "dekho", "ji", "gaya", "gayi", "gaye",
    "liya", "liye", "mila", "mile", "aaya", "aaye", "kiya", "kiye", "hua", "hue", "wapas",
    "kal", "parso", "parson", "aaj", "today", "tomorrow", "yesterday", "kab", "kitna",
    "kitne", "kitni", "total", "balance", "history", "summary", "payment", "udhaar", "credit",
    "baaki", "hisaab", "dega", "degi", "denge", "wale", "wala", "wali", "ek", "nahi", "haan",
    "ka", "ki", "ke", "ne", "ko", "se", "usne", "isne", "unhone", "usko", "uska", "uski",
    "ki", "kisne", "kaun", "kon", "kis", "kisko", "how", "what", "who", "when", "much",
    "many", "me", "my", "i", "mean", "no", "yes", "wrong", "sorry", "actually", "matlab",
    "galat", "sahi", "tarikh", "din", "baad", "pehle", "last", "recent", "show", "list",
    "entries", "entry", "transactions", "transaction", "customer", "customers", "log",
    "bola", "promise", "will", "pay", "give", "gave", "paid", "took", "received", "wada",
    "dene", "wapas", "rehne", "rahne", "mat", "cancel", "stop", "ruko", "theek", "thik",
    "pakka", "bilkul", "confirm", "ok", "okay", "right", "correct", "done", "yeah", "yep",
    "han", "ha", "haa", "hanji", "haanji", "bhai", "bhaiya", "bhaiyya", "sahab", "sahib",
    "uncle", "aunty", "auntie", "didi", "madam", "sir", "mam", "chacha", "chachi", "mama",
    "bhabhi", "is", "ye", "ik", "the", "does", "did", "will", "s", "today", "s",
}
WEEKDAYS = {
    "monday": 0, "somvar": 0, "somvaar": 0, "somwar": 0, "सोमवार": 0,
    "tuesday": 1, "mangalvar": 1, "mangalwar": 1, "mangalvaar": 1, "मंगलवार": 1,
    "wednesday": 2, "budhvar": 2, "budhwar": 2, "budhvaar": 2, "बुधवार": 2,
    "thursday": 3, "guruvar": 3, "guruwar": 3, "brihaspativar": 3, "veervar": 3, "गुरुवार": 3,
    "friday": 4, "shukravar": 4, "shukrawar": 4, "shukravaar": 4, "शुक्रवार": 4,
    "saturday": 5, "shanivar": 5, "shaniwar": 5, "shanivaar": 5, "शनिवार": 5,
    "sunday": 6, "ravivar": 6, "raviwar": 6, "itwar": 6, "itwaar": 6, "रविवार": 6,
}
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august",
          "september", "october", "november", "december", "janwari", "farwari", "march",
          "april", "mai", "jun", "julai", "agast", "sitambar", "aktubar", "navambar", "disambar"}
