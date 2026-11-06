# Changelog

## 1.4.1

### Opgelost
- Bij de topbestemmingen had de regel "Niet van toepassing" een grijze knopstijl. Die staat nu gewoon als tekst in de lijst.

## 1.4.0

### Gewijzigd
- De app wordt nu als kant-en-klaar image geleverd. Installeren en bijwerken gaat daardoor sneller, want Home Assistant hoeft niets meer zelf te bouwen.
- Installeren gaat voortaan via de repository Fonfey/hassio-addons, samen met de andere apps.

## 1.3.0

### Nieuw
- **Serverinstellingen** in het dashboard zelf: bescherming, blokkeermodus, bewaartijd, IP-adressen in de querylog, automatisch koppelen, veilig surfen (gevaarlijke en nieuw geregistreerde domeinen, typosquatting, lookalike-tekens), ouderlijk toezicht en de browser- en privacyopties.
- **Blokkeerlijsten** in het dashboard: alle beschikbare lijsten met uitleg en aantal regels, te zoeken en met één schakelaar aan of uit.
- **Apparaatinstellingen**: bescherming per apparaat aan of uit en de DNS-adressen van het apparaat om te kopiëren.
- De schakelaars voor bescherming op het dashboard werken nu echt. Bij uitzetten vraagt het dashboard eerst om bevestiging.

### Gewijzigd
- De instelling **Blokkeren en deblokkeren toestaan** heet nu **Wijzigingen toestaan** en geldt voor alle wijzigingen. Zet hem uit voor een dashboard dat alleen mag kijken.
- Alleen "Apparaat koppelen" opent nog de site van AdGuard DNS.

## 1.2.3

### Opgelost
- Geen witte scrollbalk meer rechts naast de tabbladen.
- Scrollbalken hebben nu de kleuren van het dashboard in plaats van witte Windows-scrollbalken.

## 1.2.2

### Gewijzigd
- De knop in de zijbalk is weer alleen zichtbaar voor beheerders. Gewone gebruikers zien hem niet meer.
- **Toegestane gebruikers** heet nu **Toegestane beheerders (optioneel)**. Laat de lijst leeg om alle beheerders toegang te geven, of vul namen in als niet elke beheerder het dashboard mag zien.

## 1.2.1

### Verbeterd
- Onderaan het dashboard staat nu "Ingelogd als" met je exacte gebruikersnaam, zodat je die foutloos kunt overnemen bij Toegestane gebruikers.
- De melding "Geen toegang" toont met welke gebruikersnaam iemand probeerde.
- Het logboek van de app toont bij het starten wie toegang heeft, en meldt geweigerde gebruikers met hun exacte naam.
- De uitleg bij Toegestane gebruikers vertelt waar je de gebruikersnamen vindt.

## 1.2.0

### Nieuw
- Blokkeren en deblokkeren vanuit de querylog: via het menu met drie puntjes achter elk verzoek. Het dashboard voegt daarvoor een gebruikersregel toe aan je DNS-server, net als AdGuard zelf.
- Gebruikersregels bekijken, toevoegen en verwijderen via de link "Gebruikersregels" bij je DNS-server.
- Nieuwe instelling **Blokkeren en deblokkeren toestaan**. Zet hem uit voor een dashboard dat alleen mag kijken.
- Querylog in de opzet van AdGuard: statuslabels (Geblokkeerd, Verwerkt, Toegestaan, Gewijzigd), bedrijf, apparaat, datum en client, automatisch verversen met instelbaar interval, en filters op status, type, bedrijf, server en categorie.
- Doorklikken: cijfers, bedrijven, domeinen, landen en categorieën openen de querylog of het juiste filter.
- Uitleg bij de vraagtekens verschijnt nu als tooltip, ook op een telefoon.
- Eigen icoon en logo in de App Store.

### Verbeterd
- De instellingen leggen uit waar je de API-key aanmaakt.
- Op een telefoon zijn de filters van de querylog standaard ingeklapt.

## 1.1.0

### Nieuw
- De knop in de zijbalk is nu zichtbaar voor alle gebruikers, niet alleen voor beheerders.
- Nieuwe instelling **Toegestane gebruikers**: vul Home Assistant-gebruikersnamen in om het dashboard alleen voor hen open te zetten. Laat de lijst leeg om iedereen toegang te geven. Anderen zien de melding "Geen toegang".
- De instellingen op het tabblad Configuratie hebben nu een Nederlandse naam en uitleg.

### Opgelost
- Het onderdeel Bedrijven gaf een foutmelding als AdGuard een bedrijf zonder naam teruggaf. Zo'n bedrijf staat nu als "Onbekend bedrijf" in de lijst.
- Ook servers, apparaten en domeinen zonder naam veroorzaken geen fout meer.

## 1.0.0

- Eerste versie van het AdGuard DNS Dashboard.
- Dashboard met verbruik, abonnement, geblokkeerde verzoeken en bedrijven per uur, en DNS-servers met apparaten.
- Statistieken met soorten verzoeken, topbedrijven, topbestemmingen, topdomeinen en wereldkaart.
- Bedrijvenoverzicht en querylog.
- Filters op periode (24 uur, 7 dagen, 30 dagen), server, apparaat en land.
