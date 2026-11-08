# AdGuard DNS Dashboard

[English](#english) · [Nederlands](#nederlands)

## English

Statistics of your AdGuard DNS account (adguard-dns.io) right in Home Assistant.

**Note:** this app is for **AdGuard DNS**, the cloud DNS service run by AdGuard (adguard-dns.io). It does not work with **AdGuard Home**, the free software you install and run yourself; for AdGuard Home, use the official AdGuard Home integration in Home Assistant.

The dashboard follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### Setting up

1. Create an API key at https://adguard-dns.io/en/dashboard/ : click your account at the top right, choose **User preferences** and go to **API keys**. Copy the key right away; it is shown only once.
2. Fill in on the **Configuration** tab of this app:
   - `api_key`: your AdGuard DNS API key
   - `refresh_seconds`: how often the dashboard refreshes (60 seconds by default)
   - `home_country`: country code of where you live, for the pin on the world map (`NL` by default)
3. Start the app and open **AdGuard DNS** in the sidebar.

Without an API key the app runs with sample data.

### What you see

- **Dashboard**: usage of your plan, blocked requests and companies per hour with the change compared to the previous period, and your DNS servers with their devices.
- **Statistics**: requests per period, types of requests, top companies, top destinations, top domains and a world map. Filter by period, server, device and country.
- **Companies**: the most requested and most blocked company and a table with the top companies.
- **Query log**: the latest DNS requests, searchable and filterable by blocked or allowed.

### Blocking and unblocking

In the query log, each request has a menu with three dots. Choose **Block domain** or **Unblock domain** and confirm. The dashboard then adds a user rule to the DNS server: `||domain^` to block, `@@||domain^` to allow. Through **User rules** at your DNS server you see all rules and can remove them again. Turn off **Allow changes** if the dashboard may only view.

### Changing settings

Through **Server settings** and **Blocklists** at your DNS server, and **Settings** at a device, you change everything in the dashboard itself. Only connecting a new device is done on the AdGuard DNS website. Turn off **Allow changes** if the dashboard may only view.

### Who may see the dashboard

Only Home Assistant administrators see the sidebar item. If you have several administrators and not everyone may see the dashboard, enter the usernames that may at **Allowed administrators** on the **Configuration** tab. If you leave the list empty, all administrators have access. At the bottom of the dashboard, "Signed in as" shows your exact username.

## Nederlands

Het dashboard volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels.

Statistieken van je AdGuard DNS-account (adguard-dns.io) direct in Home Assistant.

**Let op:** deze app is voor **AdGuard DNS**, de DNS-dienst in de cloud van AdGuard (adguard-dns.io). Hij werkt niet met **AdGuard Home**, de gratis software die je zelf installeert en draait; gebruik voor AdGuard Home de officiële AdGuard Home-integratie van Home Assistant.

### Instellen

1. Maak een API-key aan op https://adguard-dns.io/en/dashboard/ : klik rechtsboven op je account, kies **User preferences** en ga naar **API keys**. Kopieer de key direct, je ziet hem maar één keer.
2. Vul op het tabblad **Configuratie** van deze app in:
   - `api_key`: je AdGuard DNS API-key
   - `refresh_seconds`: hoe vaak het dashboard ververst (standaard 60 seconden)
   - `home_country`: landcode van waar je woont, voor de speld op de wereldkaart (standaard `NL`)
3. Start de app en open **AdGuard DNS** in de zijbalk.

Zonder API-key draait de app met voorbeeldgegevens.

### Wat je ziet

- **Dashboard**: verbruik van je abonnement, geblokkeerde verzoeken en bedrijven per uur met de verandering ten opzichte van de vorige periode, en je DNS-servers met hun apparaten.
- **Statistieken**: verzoeken per periode, soorten verzoeken, topbedrijven, topbestemmingen, topdomeinen en een wereldkaart. Filter op periode, server, apparaat en land.
- **Bedrijven**: het meest opgevraagde en meest geblokkeerde bedrijf en een tabel met de topbedrijven.
- **Querylog**: de laatste DNS-verzoeken, te doorzoeken en te filteren op geblokkeerd of toegestaan.

### Blokkeren en deblokkeren

In de querylog staat achter elk verzoek een menu met drie puntjes. Kies **Domein blokkeren** of **Domein deblokkeren** en bevestig. Het dashboard voegt dan een gebruikersregel toe aan de DNS-server: `||domein^` om te blokkeren, `@@||domein^` om toe te staan. Via **Gebruikersregels** bij je DNS-server zie je alle regels en kun je ze weer verwijderen. Zet **Blokkeren en deblokkeren toestaan** uit als het dashboard alleen mag kijken.

### Instellingen wijzigen

Via **Serverinstellingen** en **Blokkeerlijsten** bij je DNS-server, en **Instellingen** bij een apparaat, wijzig je alles in het dashboard zelf. Alleen een nieuw apparaat koppelen doe je op de site van AdGuard DNS. Zet **Wijzigingen toestaan** uit als het dashboard alleen mag kijken.

### Wie mag het dashboard zien

Alleen beheerders van Home Assistant zien de knop in de zijbalk. Heb je meerdere beheerders en mag niet iedereen het dashboard zien, vul dan op het tabblad **Configuratie** bij **Toegestane beheerders** de gebruikersnamen in die wel mogen. Laat je de lijst leeg, dan hebben alle beheerders toegang. Onderaan het dashboard staat "Ingelogd als" met je exacte gebruikersnaam.
