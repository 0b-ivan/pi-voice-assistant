"""Spoken device commands: WLAN off/on, sleep, reboot and shut down the Pi.

WLAN switches at once; "geh schlafen" puts the unit into its sleep state
(screen and LED off, the wake word keeps listening) once that is said.
Reboot and shutdown ask back first: the operator
confirms by saying "bestätigt" (or "ja") or with button E within
CONFIRM_SECONDS; "nein"/"abbrechen" or button B cancel, anything else drops
the request and is answered as usual. The Pi keeps the pending request and
sends it with its status snapshot (``pending``), so the server can recognize
the spoken confirmation. The server refuses all of this to an unrecognized
voice. "Starte neu" in the maintenance mode stays the maintenance action
(confirmed with E); the server itself is only restarted from there.

Text only (no hardware): ptt.py switches WLAN (rfkill), requests the reboot
(maintenance worker, else systemctl) and powers off (polkit rule).
"""
import re

OPS = ('wlan_off', 'wlan_on', 'sleep', 'reboot', 'shutdown')
CONFIRM_OPS = ('reboot', 'shutdown')
CONFIRM_SECONDS = 20.0   # from the question; the answer's recording starts well before

_WLAN = re.compile(r'\b(wlan|w lan|wifi|wi fi|wireless lan|funkmodul|funk)\b')
_OFF = re.compile(r'\b(aus|ab|deaktivier\w*|abschalt\w*|ausschalt\w*|trenn\w*)\b')
_ON = re.compile(r'\b(an|ein|aktivier\w*|einschalt\w*|anschalt\w*)\b')
_SHUTDOWN = re.compile(
    r'\b((her)?(unter|runter) ?fahr\w*'
    r'|fahr\w* (dich |das system |den pi |die einheit )?(her)?(unter|runter)'
    r'|schalt\w* dich (selbst )?(aus|ab)|mach\w* dich (selbst )?aus|shut ?down'
    r'|terminier\w* dich|selbst ?terminier\w*|geh\w* sterben|stirb'
    r'|zerstör\w* dich|selbst ?zerstör\w*)\b')
_SLEEP = re.compile(
    r'\b(geh\w* (jetzt )?schlafen|schlafen gehen|leg dich schlafen|schlaf (jetzt )?ein'
    r'|ruhe ?modus|schlaf ?modus|energie ?spar ?modus|stand ?by)\b')
_REBOOT = re.compile(r'\b(neu ?start\w*|starte?\b.*\bneu|reboot\w*)\b')
_SERVER = re.compile(r'\b(server|kogitator|ct ?107|container)\b')
# The whole answer must be one of these (plus fillers): "mach das licht an" never confirms.
_FILLER = re.compile(r'\b(bitte|proximus|servitor|billy|jetzt|ok|okay)\b')
_CONFIRM = re.compile(r'(ja|jawohl|ja mach|ja mach das|bestätig\w*|ich bestätige|bestätigung|'
                      r'ausführen|führe? (es |das )?aus|mach (es|das)|affirmativ|positiv|korrekt)')
_CANCEL = re.compile(r'(nein|nee|abbrechen|abbruch|brich ab|halt|negativ|doch nicht|'
                     r'nicht ausführen|stopp?)')


def command(text):
    """'wlan_off', 'wlan_on', 'sleep', 'reboot', 'shutdown' or None. ``text`` is normalized
    (lower case, no punctuation). Reboot of the server is not ours (maintenance)."""
    text = str(text).strip()
    if not text or len(text.split()) > 8:
        return None
    if _WLAN.search(text):
        if _OFF.search(text):
            return 'wlan_off'
        if _ON.search(text):
            return 'wlan_on'
        return None
    if _SERVER.search(text):
        return None
    if _SHUTDOWN.search(text):
        return 'shutdown'
    if _SLEEP.search(text):
        return 'sleep'
    if _REBOOT.search(text):
        return 'reboot'
    return None


def answer(text):
    """Reply to a pending question: 'confirm', 'cancel' or None (something else)."""
    text = ' '.join(_FILLER.sub(' ', str(text)).split())
    if _CONFIRM.fullmatch(text):
        return 'confirm'
    if _CANCEL.fullmatch(text):
        return 'cancel'
    return None


def _billy(style):
    return style in ('billy', 'billy_full')


ASK = {
    'reboot': "Neustart der Einheit angefordert. Bestätigen: Bestätigt oder Taste E.",
    'shutdown': ("Herunterfahren angefordert. Danach nur per Schalter wieder aktiv. "
                 "Bestätigen: Bestätigt oder Taste E."),
}
ASK_BILLY = {
    'reboot': "Neustart? Sag bestätigt oder drück E.",
    'shutdown': "Ganz ausschalten? Dann komm ich nur per Schalter wieder. Sag bestätigt oder drück E.",
}
START = {
    'wlan_off': "WLAN deaktiviert. Lokaler Betrieb.",
    'wlan_on': "WLAN aktiviert. Verbindung wird aufgebaut.",
    'sleep': "Ruhemodus. Aktivierungswort bleibt aktiv.",
    'reboot': "Neustart eingeleitet.",
    'shutdown': "Einheit fährt herunter.",
}
START_FULL = {
    'sleep': "Ruhemodus. Der Maschinengeist schlummert, das Auspex wacht.",
    'reboot': "Neustart eingeleitet. Der Maschinengeist ruht kurz.",
    'shutdown': "Einheit fährt herunter. Der Maschinengeist schläft. Lob dem Omnissiah.",
}
START_BILLY = {
    'wlan_off': "WLAN ist aus. Ich mach lokal weiter.",
    'wlan_on': "WLAN geht an, Moment.",
    'sleep': "Ich hau mich hin. Ruf mich, wenn was ist.",
    'reboot': "Ich starte neu. Bis gleich.",
    'shutdown': "Ich mach dann mal aus.",
}
ALREADY = {'wlan_off': "WLAN ist bereits deaktiviert.", 'wlan_on': "WLAN ist bereits aktiv."}
ALREADY_BILLY = {'wlan_off': "WLAN ist schon aus.", 'wlan_on': "WLAN ist schon an."}
CANCELLED = "Abgebrochen."
CANCELLED_BILLY = "Okay, lass ich."
DENIED = "Stimme nicht als Bediener erkannt. Befehl verweigert."
DENIED_BILLY = "Dich kenn ich nicht. Das macht nur der Boss."
AUTO_WLAN = "Anfrage braucht Netz. WLAN wird aktiviert."
AUTO_WLAN_BILLY = "Dafür brauch ich Netz, ich mach das WLAN an."
AUTO_WLAN_RETRY = "WLAN aktiviert. Anfrage in einigen Sekunden wiederholen."
AUTO_WLAN_RETRY_BILLY = "WLAN ist an. Frag gleich nochmal."
FAILED = "Befehl fehlgeschlagen. Protokoll prüfen."
FAILED_BILLY = "Hat nicht geklappt. Schau ins Protokoll."


def ask_text(op, style='off'):
    return (ASK_BILLY if _billy(style) else ASK)[op]


def start_text(op, style='off'):
    if _billy(style):
        return START_BILLY[op]
    if style == 'full' and op in START_FULL:
        return START_FULL[op]
    return START[op]


def already_text(op, style='off'):
    return (ALREADY_BILLY if _billy(style) else ALREADY)[op]


def cancelled_text(style='off'):
    return CANCELLED_BILLY if _billy(style) else CANCELLED


def denied_text(style='off'):
    return DENIED_BILLY if _billy(style) else DENIED


def auto_wlan_text(style='off', retry=False):
    if retry:
        return AUTO_WLAN_RETRY_BILLY if _billy(style) else AUTO_WLAN_RETRY
    return AUTO_WLAN_BILLY if _billy(style) else AUTO_WLAN


def failed_text(style='off'):
    return FAILED_BILLY if _billy(style) else FAILED


def reply(op, wlan=None, style='off'):
    """The sentence for a command turn: question, 'already', or what happens now."""
    if op in CONFIRM_OPS:
        return ask_text(op, style)
    if (op == 'wlan_off' and wlan == 'off') or (op == 'wlan_on' and wlan == 'on'):
        return already_text(op, style)
    return start_text(op, style)
