# Persönlichkeit: Servitor und Billy

**SERVITOR** antwortet als mechanische Diensteinheit Proximus; **BILLY** als menschliches Engramm. Beide haben wählbare Lore-Stufen **AUS / DEZENT / VOLL** sowie einen natürlichen oder maschinellen Stimmeffekt. Fakten haben Vorrang vor Rollenprosa.

Im Menü **Persönlichkeit** werden Sprechstil, Stimmeffekt, Lore und Gefühle eingestellt. Die Wahl übersteht einen Neustart unter `/var/lib/pi-ptt/settings.json`. Umsetzung: [`src/llm.py`](../../src/llm.py), [`src/mood.py`](../../src/mood.py) und [Piper](speech.md).

Die Hintergrundgeschichte steht unter [Billy-Lore](../concepts/lore-blazkowicz.md). [Persönlichkeit und SPX/1](../concepts/persoenlichkeit-und-protokoll.md) beschreibt auch **geplante**, bislang nicht vollständig implementierte Funktionen wie Logbuch, Outbox, Raumgesprächserkennung und Unterbewusstsein.
