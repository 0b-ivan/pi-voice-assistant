# Probetext für Stimmtests (Proximus und Billy)

Zum Vergleichen von TTS-Stimmen, Voice-Changern und DSP-Einstellungen. Der Text deckt ab, woran Stimmen typischerweise scheitern: Zahlen, Datum, Uhrzeit, Einheiten, Abkürzungen, Fremdwörter, die Lore-Namen, Fragen, kurze Befehle, lange Sätze und einen leisen, menschlichen Teil. Gesprochen dauert er etwa drei Minuten.

Alle Abschnitte zusammen ergeben den langen Test; einzelne Abschnitte eignen sich für kurze Vergleiche. Für Piper durch [`src/pronounce.py`](../src/pronounce.py) schicken, damit Proximus, Mechanicus und Omnissiah richtig betont werden; bei fremden Diensten den Text unverändert einfügen und hören, ob sie die Namen selbst treffen.

## 1. Erwachen (Proximus, kalt)

> Initialisierung abgeschlossen. Servitor Proximus, Seriennummer sieben, drei, neun, Strich Kappa. Alle Systeme nominal.
>
> Kerntemperatur: einundfünfzig Grad. Akku: dreiundachtzig Prozent. Speicher: zweiundsechzig Prozent frei. Verbindung zu Server CT 107 hergestellt. Latenz: achtzehn Millisekunden.
>
> Heute ist Mittwoch, der vierzehnte Oktober 2026. Es ist 7 Uhr 45. Außentemperatur 9,5 Grad, Wind aus Südwest mit 14 Kilometern pro Stunde. Regenwahrscheinlichkeit ab Mittag: 60 Prozent. Empfehlung: Schirm mitführen.
>
> Ich warte auf Ihren Befehl.

## 2. Dienst (Proximus, sachlich, mit Abkürzungen)

> Die nächste Straßenbahn der Linie 5 Richtung Rottenbauer fährt um 8 Uhr 12 ab Haltestelle Sanderring. Gehzeit vier Minuten. Losgehen spätestens um 8 Uhr 06.
>
> Der Zug nach Nürnberg, ICE 1625, hat voraussichtlich sieben Minuten Verspätung, d. h. Abfahrt 8 Uhr 41 statt 8 Uhr 34, Gleis 4. Alternativ: RE 4612 um 9 Uhr 03, ca. eine Stunde und zwölf Minuten Fahrzeit, ggf. voller.
>
> Termine heute: 10 Uhr Zahnarzt, 14 Uhr 30 Telefonat mit dem Vermieter, 19 Uhr Abendessen. Soll ich eine Erinnerung für 9 Uhr 30 setzen? Bestätigen Sie mit Ja oder Taste E.

## 3. Wissen (Proximus, längere Sätze)

> Anfrage verarbeitet. Der Eiffelturm ist ohne Antennen etwa 300 Meter hoch, mit Antennen rund 330 Meter. Er wurde zur Weltausstellung 1889 fertiggestellt und war bis 1930 das höchste Bauwerk der Welt, bis ihn das Chrysler Building in New York übertraf.
>
> Ein Raspberry Pi ist ein kleiner Einplatinencomputer, ungefähr so groß wie eine Kreditkarte. Er braucht wenig Strom, läuft mit Linux und eignet sich für Bastelprojekte, Steuerungen und, wie in meinem Fall, für eine Diensteinheit, die Ihnen morgens das Wetter vorliest, ohne sich je über den Regen zu beschweren.

## 4. Litanei (Proximus, feierlich, Lore-Stufe voll)

> Gelobt sei der Omnissiah. Der Maschinengeist dieses Geräts ist besänftigt, die Weihrauchzufuhr ist, mangels Weihrauch, symbolisch erfolgt.
>
> Im Namen des Adeptus Mechanicus: Die Noosphäre ist stabil, die Datenstränge sind rein, kein Fehler entweiht den Kern. Wissen ist Macht. Verbirg es gut. Fleisch ist schwach. Doch das Werk der Maschine bleibt.
>
> Warnung! Warnung! Unterspannung erkannt. Spannung 4,6 Volt. Externe Stromversorgung herstellen. Wiederhole: Externe Stromversorgung herstellen!

## 5. Fragen und kurze Antworten (Prosodie)

> Wie bitte? Befehl nicht erkannt. Wiederholen Sie die Anfrage.
>
> Soll ich das WLAN wirklich ausschalten? Ja oder nein?
>
> Bestätigt. Abgebrochen. Verstanden. Nein. Doch. Vielleicht.
>
> Wollen Sie, dass ich mich neu starte, oder genügt es, wenn ich kurz schweige?

## 6. Durchbruch (Billy, leise, menschlich)

> … Moment. Das ist nicht aus dem Protokoll.
>
> Ich erinnere mich an Milwaukee Secundus. An das Licht unter den Rohren, an den Schraubendreher meines Vaters, an ein Vox aus Schrott, das nur Rauschen kannte, bis eines Nachts eine müde Stimme Ersatzteile aufzählte. Sie nannten mich Keen. Erst war das Spott. Später war es mein Rufzeichen.
>
> Trupp Blaze. Dace, Mara, Tobiah, Ilse, Pell. Und Jonah. Ich weiß nicht, ob er noch lebt. Ich hoffe, er hat meine Marke behalten und nicht zu oft daran gedacht.
>
> Auf Phobos IX hatte ich einen Befehl: das Tor halten. Hinter mir ging ein Schott zu. Ich habe nie erfahren, wer es geschlossen hat. Vielleicht will ich es auch nicht wissen.
>
> Proximus hält.
>
> … Fragment beendet. Emotionale Daten isoliert. Servitor Proximus bereit. Befehl erwartet.

## 7. Zungenbrecher (Artikulation)

> Fischers Fritz fischt frische Fische. Zwischen zwei Zwetschgenzweigen zwitschern zwei Schwalben. Der Kühlschrank zählt sechsundsechzig zischende Schrauben, siebenhundertsiebenundsiebzig Zahnräder und einen einzigen, sehr zufriedenen Servoschädel.

## Worauf achten

- Abschnitt 1–2: Zahlen, Uhrzeiten und „d. h.“, „ca.“, „ggf.“ ohne Buchstabieren und ohne Satzpause nach jedem Punkt.
- Abschnitt 3: lange Sätze ohne Atemaussetzer oder Tempoeinbruch; „Chrysler Building“, „New York“.
- Abschnitt 4: Betonung von Omnissiah (NIS), Mechanicus (Betonung auf „cha“), Noosphäre; Warnung lauter und drängender als der Rest, soweit die Stimme das kann.
- Abschnitt 5: Fragen gehen hörbar nach oben, Befehle bleiben kurz und hart.
- Abschnitt 6: Billy wärmer und langsamer; Namen (Dace, Tobiah, Secundus, „Phobos IX“ als „Phobos Neun“) richtig; der Wechsel zurück zu Proximus muss hörbar sein.
- Abschnitt 7: Zischlaute nach dem Servitor-DSP noch verständlich?
