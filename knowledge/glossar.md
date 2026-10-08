# Glossar Datenmodell (trendic DB)

## Tabellen
- MESSMITTEL: ein Datensatz je Messmittel (Instrument) eines Kunden, mit letzter/nächster Prüfung, Prüfintervall, Fälligkeitstyp, letzter Bewertung, Messmittelgruppe/-typ und Messraum (Labor).
- KALIBRIERUNGEN: Kalibrierhistorie seit 2024 (Beginn, Ende, Bewertung, Prüfungsart Werk/DAkkS, Kunde, Messraum).
- AUFTRAGSPOSITIONEN: eine Position je eingesendetem Messmittel in einem Auftrag (nur ca. 24 Monate). dateErfasst = Eingang erfasst, dateDlErbracht = Dienstleistung erbracht, dateAusgeliefert = ausgeliefert.
- DIENSTLEISTUNGEN: einzelne Leistungen je Auftragsposition (KALIBRIERUNG_WERK, KALIBRIERUNG_DAKKS, ZUSATZLEISTUNG, SIGNIERUNG, SCHMELZTAUCHEN ...), mit Artikelnummer.
- ArtikelnummerZeit: Bearbeitungszeit in Minuten je Artikelnummer.
- Kunde_Branche: Zuordnung Kunde zu einer von 15 Branchen.
- Ist_Stunden / Soll-Kapa: Anwesenheits-, Krank-, Urlaubs- und sonstige Stunden je Kostenstelle/Messraum und Tag.

## Bewertungen
- EINSATZFAEHIG: in Ordnung.
- BEDINGT_EINSATZFAEHIG_GELB / _BLAU: eingeschränkt einsatzfähig.
- NICHT_EINSATZFAEHIG: n.i.O. (nicht in Ordnung); wird in den Analysen als nicht mehr aktiv behandelt.
- ISTMASS: Istmaß wurde dokumentiert.

## Fälligkeit
- FAELLIGKEIT_TYP LAUT_KALIBRIERSCHEIN: nächste Fälligkeit laut Kalibrierschein.
- AB_ERSTBENUTZUNG: Intervall läuft ab erster Benutzung.
- INDIVIDUELL / MAXIMALE_NUTZUNGEN: Sonderfälle.
- Leer (ca. 60 %): kein Fälligkeitstyp erfasst. Fehlende Fälligkeiten werden aus der eigenen Kalibrierhistorie (reales Intervall) oder dem Prüfintervall geschätzt (Spalte faellig_quelle: erfasst / historie / intervall).
- FAELLIGKEIT_STOP = true: Messmittel wird nicht mehr erwartet.
- EINHEIT_PRUEFINTERVALL: 1 = Jahre, 2 = Monate, 3 = Wochen, 4 = Tage. Median-Intervall 12 Monate.

## Prüfungsart
- Werk (Werkskalibrierung): Standardkalibrierung.
- DAkkS: akkreditierte Kalibrierung nach DAkkS, höherwertig; in regulierten Branchen (Luftfahrt, Medical, Pharma, Defence) häufig gefordert. Werkskunden in solchen Branchen sind Upsell-Kandidaten.

## Abgeleitete Kennzahlen
- Rücklaufquote 12M: Kalibrierungen der letzten 12 Monate geteilt durch (diese + überfällige, nicht eingegangene Messmittel). Niedrig = Kunde schickt fällige Messmittel nicht (mehr) ein.
- Überfällig: aktives Messmittel, Fälligkeit vor 30 bis 730 Tagen, seitdem nicht kalibriert.
- Trend: Kalibrierungen Jan-Sep 2026 gegenüber Jan-Sep 2025.
- Lücken gegenüber Branchenportfolio: Messmittelgruppen, die in der Branche mindestens 5 % ausmachen, beim Kunden aber weniger als ein Viertel davon. Hinweis auf Messmittel, die woanders kalibriert werden.
- Potenzial (Std.): geschätzte Arbeitsstunden der fälligen und überfälligen Messmittel des Kunden.
- Risiko-Score: vorläufige Regel (Trend, Rücklaufquote, Tage seit letzter Kalibrierung), wird durch ein trainiertes Churn-Modell ersetzt.
