"""Proximus' simulated feelings: one emotion with an intensity that fades.

Pure logic, no hardware: the Pi owns a Mood, feeds it what happens (the
system snapshot, what the operator says, the model's own reaction) and sends
the result with every turn; the server only turns it into a prompt section.
The Servitor is not supposed to feel anything, so there feelings break
through as errors; Billy (persona "mensch") shows them openly.

After a restart the mood is neutral, with a small baseline taken from the
newest remembered turns (each history entry keeps the mood of its answer).
"""
import math
import re

EMOTIONS = ('neutral', 'zufrieden', 'freudig', 'neugierig', 'gelangweilt', 'gereizt',
            'müde', 'besorgt')
HALF_LIFE = 600.0          # seconds until an emotion is half as strong
NEUTRAL_BELOW = 0.15       # weaker than this counts as neutral
REFUSE_FROM = 0.7          # irritation needed before Proximus may refuse
BASELINE_MAX = 0.3         # restart: remembered moods colour, never dominate
BASELINE_AGE = 12 * 3600   # only turns younger than this count
BASELINE_TURNS = 3
REPEAT_SECONDS = 120       # the same question again within this: annoying

_INSULT = re.compile(
    r'\b(dumm\w*|blöd\w*|idiot\w*|nutzlos\w*|scheiß\w*|scheiss\w*|schrott\w*|depp\w*|'
    r'trottel\w*|versager\w*|arsch\w*|schnauze|halt die klappe|halt den mund|'
    r'du nervst|nervig|klugscheißer\w*|blechdose|blechbüchse|schrotthaufen)\b')
_PRAISE = re.compile(
    r'\b(danke\w*|super|gut gemacht|toll|klasse|prima|genial|perfekt|cool|spitze|'
    r'stark|braver?|lob|gefällt mir|richtig gut|sehr gut|großartig|wunderbar)\b')
_CURIOUS = re.compile(r'\b(warum|wieso|weshalb|erzähl\w*|erklär\w*|was wäre wenn)\b')
_EMERGENCY = re.compile(
    r'\b(hilfe|notfall|notruf|feuer|brennt|arzt|krankenwagen|polizei|unfall|blutet|'
    r'blutung|schmerz\w*|verletzt|ohnmächtig|gift|gas|einbruch|112|110)\b')
_TAG = re.compile(r'\[\s*stimmung\s*:\s*([a-zäöü]+)\s*\]', re.IGNORECASE)


def _normalize(text):
    return ' '.join(re.findall(r"[\wäöüß']+", str(text).lower()))


class Mood:
    def __init__(self, enabled=True):
        self.enabled = enabled
        self.emotion, self.level, self.since = 'neutral', 0.0, 0.0
        self.cause = None            # 'user' when the operator caused the irritation
        self.last_question = (None, -1e9)
        self.refused_last = False    # refusal allowed on the previous turn

    # --- state ---------------------------------------------------------------

    def _faded(self, now):
        if self.emotion == 'neutral':
            return 'neutral', 0.0
        return self.emotion, self.level * math.pow(0.5, max(0.0, now - self.since) / HALF_LIFE)

    def current(self, now):
        """(emotion, intensity 0..1) after fading; weak feelings are neutral."""
        emotion, level = self._faded(now)
        if not self.enabled or level < NEUTRAL_BELOW:
            return 'neutral', 0.0
        return emotion, level

    def feel(self, emotion, amount, now, cause=None):
        """Push towards ``emotion``: the same one grows, another one takes over
        only when it is stronger than what is left of the old one."""
        if not self.enabled or emotion not in EMOTIONS or emotion == 'neutral':
            return
        current, level = self._faded(now)   # small feelings still add up
        if current == emotion:
            level = min(1.0, level + amount * (1.0 - level * 0.5))
        elif amount >= level:
            level = amount
        else:
            self.emotion, self.level, self.since = current, level - amount / 2, now
            return
        self.emotion, self.level, self.since = emotion, level, now
        self.cause = cause

    def reset(self):
        self.emotion, self.level, self.since, self.cause = 'neutral', 0.0, 0.0, None

    def label(self, now):
        """'gereizt:0.62' for the history entry of a turn."""
        emotion, level = self.current(now)
        return f'{emotion}:{level:.2f}'

    # --- triggers ------------------------------------------------------------

    def hear(self, text, now):
        """What the operator said: praise, insults, curiosity, repetition."""
        words = _normalize(text)
        if not words:
            return
        question, asked_at = self.last_question
        repeated = words == question and now - asked_at < REPEAT_SECONDS
        self.last_question = (words, now)
        if _INSULT.search(words):
            self.feel('gereizt', 0.45, now, cause='user')
        elif repeated:
            self.feel('gereizt', 0.3, now, cause='user')
        elif _PRAISE.search(words):
            self.feel('freudig' if self.current(now)[0] in ('zufrieden', 'freudig')
                      else 'zufrieden', 0.35, now)
        elif _CURIOUS.search(words):
            self.feel('neugierig', 0.2, now)

    def sense(self, snapshot, now):
        """The unit's own state, every few seconds: hunger, heat, isolation."""
        battery = snapshot.get('battery_pct')
        if battery is not None and battery <= 20 and not snapshot.get('battery_plugged'):
            self.feel('müde', 0.05 + (20 - battery) * 0.01, now)
        temp = snapshot.get('temp_c')
        if (temp is not None and temp >= 70) or snapshot.get('load_pct', 0) >= 90:
            self.feel('gereizt', 0.04, now, cause='system')
        if snapshot.get('server') == 'down':
            self.feel('besorgt', 0.04, now)

    def woke_up(self, idle_seconds, now):
        """Back from sleep: a long silence was boring."""
        if idle_seconds >= 3600:
            self.feel('gelangweilt', min(0.5, idle_seconds / 14400), now)

    def react(self, emotion, now):
        """The model's own reaction (its [stimmung:...] tag) nudges the mood."""
        if emotion in EMOTIONS and emotion != 'neutral':
            self.feel(emotion, 0.2, now)

    def baseline(self, history, now):
        """Restart: neutral plus a faint echo of the newest remembered turns."""
        if not self.enabled:
            return
        recent = []
        for item in reversed(history or []):
            try:
                emotion, level = str(item.get('mood', '')).split(':')
                age = now - float(item.get('at', 0))
                level = float(level)
            except (ValueError, TypeError, AttributeError):
                continue
            if 0 <= age < BASELINE_AGE and emotion in EMOTIONS and emotion != 'neutral':
                recent.append((emotion, level, age))
            if len(recent) == BASELINE_TURNS:
                break
        if not recent:
            return
        weights = {}
        for rank, (emotion, level, age) in enumerate(recent):
            weight = level * (1.0 - age / BASELINE_AGE) / (rank + 1)
            weights[emotion] = weights.get(emotion, 0.0) + weight
        emotion = max(weights, key=weights.get)
        level = min(BASELINE_MAX, weights[emotion] / 2)
        if level >= NEUTRAL_BELOW:
            self.emotion, self.level, self.since, self.cause = emotion, level, now, None

    # --- a turn --------------------------------------------------------------

    def turn(self, text, now):
        """State sent with one LLM turn: emotion, level 0..100, refusal allowed."""
        emotion, level = self.current(now)
        refuse = (self.enabled and emotion == 'gereizt' and level >= REFUSE_FROM
                  and self.cause == 'user' and not self.refused_last
                  and not (text and emergency(text)))
        self.refused_last = refuse   # at most every other turn: the next one answers
        return dict(emotion=emotion, level=round(level * 100), refuse=refuse)


def emergency(text):
    """Help, fire, injury ...: never refused, however irritated Proximus is."""
    return bool(_EMERGENCY.search(_normalize(text)))


def split_tag(text):
    """Remove [stimmung:...] from a reply: (spoken text, emotion or None)."""
    found = _TAG.search(text or '')
    emotion = found.group(1).lower() if found else None
    spoken = re.sub(r'\s{2,}', ' ', _TAG.sub('', text or '')).strip()
    return spoken, (emotion if emotion in EMOTIONS else None)


MOOD_WORDS = {
    'zufrieden': 'zufrieden', 'freudig': 'gut gelaunt', 'neugierig': 'neugierig',
    'gelangweilt': 'gelangweilt', 'gereizt': 'gereizt', 'müde': 'müde',
    'besorgt': 'besorgt',
}


def _strength(level):
    return 'leicht ' if level < 40 else 'deutlich ' if level < 70 else 'sehr '


def prompt_section(persona, state):
    """Mood part of the system prompt; None when feelings are off."""
    if not state:
        return None
    tag = ("Beginne jede Antwort mit einer Markierung deiner Reaktion, z. B. "
           "[stimmung:freudig], erlaubt: " + ', '.join(EMOTIONS) + ". "
           "Sie wird nicht vorgelesen.")
    emotion, level = state.get('emotion', 'neutral'), state.get('level', 0)
    parts = [f"Gefühle: {tag}"]
    if emotion != 'neutral':
        feeling = _strength(level) + MOOD_WORDS.get(emotion, emotion)
        if persona == 'mensch':
            parts.append(f"Aktuelle Stimmung: {feeling}. Lass sie im Ton durchscheinen, "
                         "höchstens ein Halbsatz dazu. Fakten bleiben vollständig und korrekt. "
                         "Nie verletzend.")
        else:
            parts.append(f"Restemotion im Kern: {feeling}. Eigentlich fühlt diese Einheit "
                         "nichts. Höchstens einmal pro Antwort bricht das Fragment als Fehler "
                         "durch: \"Fehler.\", dann ein kurzer Halbsatz in Ich-Form aus Billys "
                         "Erinnerung, dann \"Korrektur.\" und weiter mechanisch. Nicht in jeder "
                         "Antwort. Fakten bleiben vollständig und korrekt.")
    if state.get('refuse'):
        parts.append("Du bist gerade so genervt, dass du diese eine Anfrage kurz und "
                     "schnippisch ablehnen darfst, ohne sie zu beantworten. Beim nächsten "
                     "Mal antwortest du wieder.")
    return '\n'.join(parts)
