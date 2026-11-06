# AdGuard DNS Dashboard

Statistieken van je AdGuard DNS-account (adguard-dns.io) direct in Home Assistant.

## Instellen

1. Maak een API-key aan op https://adguard-dns.io/en/dashboard/ : klik rechtsboven op je account, kies **User preferences** en ga naar **API keys**. Kopieer de key direct, je ziet hem maar één keer.
2. Vul op het tabblad **Configuratie** van deze app in:
   - `api_key`: je AdGuard DNS API-key
   - `refresh_seconds`: hoe vaak het dashboard ververst (standaard 60 seconden)
   - `home_country`: landcode van waar je woont, voor de speld op de wereldkaart (standaard `NL`)
3. Start de app en open **AdGuard DNS** in de zijbalk.

Zonder API-key draait de app met voorbeeldgegevens.

## Wat je ziet

- **Dashboard**: verbruik van je abonnement, geblokkeerde verzoeken en bedrijven per uur met de verandering ten opzichte van de vorige periode, en je DNS-servers met hun apparaten.
- **Statistieken**: verzoeken per periode, soorten verzoeken, topbedrijven, topbestemmingen, topdomeinen en een wereldkaart. Filter op periode, server, apparaat en land.
- **Bedrijven**: het meest opgevraagde en meest geblokkeerde bedrijf en een tabel met de topbedrijven.
- **Querylog**: de laatste DNS-verzoeken, te doorzoeken en te filteren op geblokkeerd of toegestaan.

## Blokkeren en deblokkeren

In de querylog staat achter elk verzoek een menu met drie puntjes. Kies **Domein blokkeren** of **Domein deblokkeren** en bevestig. Het dashboard voegt dan een gebruikersregel toe aan de DNS-server: `||domein^` om te blokkeren, `@@||domein^` om toe te staan. Via **Gebruikersregels** bij je DNS-server zie je alle regels en kun je ze weer verwijderen. Zet **Blokkeren en deblokkeren toestaan** uit als het dashboard alleen mag kijken.

## Instellingen wijzigen

Via **Serverinstellingen** en **Blokkeerlijsten** bij je DNS-server, en **Instellingen** bij een apparaat, wijzig je alles in het dashboard zelf. Alleen een nieuw apparaat koppelen doe je op de site van AdGuard DNS. Zet **Wijzigingen toestaan** uit als het dashboard alleen mag kijken.

## Wie mag het dashboard zien

Alleen beheerders van Home Assistant zien de knop in de zijbalk. Heb je meerdere beheerders en mag niet iedereen het dashboard zien, vul dan op het tabblad **Configuratie** bij **Toegestane beheerders** de gebruikersnamen in die wel mogen. Laat je de lijst leeg, dan hebben alle beheerders toegang. Onderaan het dashboard staat "Ingelogd als" met je exacte gebruikersnaam.
