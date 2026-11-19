# AdGuard Dashboard

[English](#english) · [Nederlands](#nederlands)

## English

Statistics, query log and blocking for AdGuard right in Home Assistant. The app works with either of the two AdGuard products; you choose which one at **Source** on the **Configuration** tab:

| Source | What it is | What the app needs |
| --- | --- | --- |
| `cloud` | **AdGuard DNS**, the cloud DNS service at adguard-dns.io | An API key |
| `home` | **AdGuard Home**, the free software you run yourself, for example as an app in Home Assistant or on a Raspberry Pi | The address, username and password of AdGuard Home |

The dashboard follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### Setting up AdGuard DNS (cloud)

1. Create an API key at https://adguard-dns.io/en/dashboard/ : click your account at the top right, choose **User preferences** and go to **API keys**. Copy the key right away; it is shown only once.
2. Fill in on the **Configuration** tab of this app:
   - `source`: `cloud`
   - `api_key`: your AdGuard DNS API key
3. Start the app and open **AdGuard** in the sidebar.

### Setting up AdGuard Home

1. Fill in on the **Configuration** tab of this app:
   - `source`: `home`
   - `home_url`: the address of the AdGuard Home web interface, for example `http://192.168.1.2:3000`
   - `home_username` and `home_password`: the username and password you log in to AdGuard Home with
2. Start the app and open **AdGuard** in the sidebar.

Check the password carefully: after 5 wrong attempts AdGuard Home blocks logging in for 15 minutes.

**Using the AdGuard Home app in Home Assistant?** That app is normally only reachable through Home Assistant itself. Open the AdGuard Home app, go to the **Configuration** tab and set a port at **Web interface** under **Network**, for example `3000`. Restart AdGuard Home and fill in `http://<IP address of Home Assistant>:3000` at `home_url`, with the username and password of AdGuard Home.

Which situation is yours? If AdGuard Home runs on another device, such as a Raspberry Pi, a NAS or a server, this app puts its screen in the Home Assistant sidebar. If AdGuard Home runs as an app inside Home Assistant itself, that app already has its own sidebar item, so you don't need this app for it.

Without an API key or address, the app runs with sample data.

### Other settings

- `refresh_seconds`: how often the dashboard refreshes (60 seconds by default)
- `home_country`: country code of where you live, for the pin on the world map (`NL` by default, AdGuard DNS only)
- `allow_blocking`: whether the dashboard may change things; turn off for a view-only dashboard
- `allowed_users`: see "Who may see the dashboard" below

### What you see

With **AdGuard DNS**:

- **Dashboard**: usage of your plan, blocked requests and companies per hour with the change compared to the previous period, and your DNS servers with their devices.
- **Statistics**: requests per period, types of requests, top companies, top destinations, top domains and a world map. Filter by period, server, device and country.
- **Companies**: the most requested and most blocked company and a table with the top companies.
- **Query log**: the latest DNS requests, searchable and filterable by blocked or allowed.

With **AdGuard Home** you see the original screen of AdGuard Home itself, in the Home Assistant sidebar and logged in automatically. Everything AdGuard Home offers works as usual: dashboard, settings, filters, query log and setup guide. The language follows the setting in AdGuard Home (bottom of its page).

### Blocking and unblocking (AdGuard DNS)

In the query log, each request has a menu with three dots. Choose **Block domain** or **Unblock domain** and confirm. The dashboard then adds a user rule: `||domain^` to block, `@@||domain^` to allow. Through **User rules** you see all rules and can remove them again. Turn off **Allow changes** if the dashboard may only view.

### Changing settings

- **AdGuard DNS**: through **Server settings** and **Blocklists** at your DNS server, and **Settings** at a device, you change everything in the dashboard itself. Only connecting a new device is done on the AdGuard DNS website.
- **AdGuard Home**: you change everything in the original AdGuard Home screen, as usual. With **Allow changes** turned off, the screen is view-only.

### Who may see the dashboard

Only Home Assistant administrators see the sidebar item. If you have several administrators and not everyone may see the dashboard, enter the usernames that may at **Allowed administrators** on the **Configuration** tab. If you leave the list empty, all administrators have access. At the bottom of the dashboard, "Signed in as" shows your exact username.

## Nederlands

Statistieken, querylog en blokkeren voor AdGuard, direct in Home Assistant. De app werkt met allebei de producten van AdGuard; welke je gebruikt, kies je bij **Bron** op het tabblad **Configuratie**:

| Bron | Wat het is | Wat de app nodig heeft |
| --- | --- | --- |
| `cloud` | **AdGuard DNS**, de DNS-dienst in de cloud op adguard-dns.io | Een API-key |
| `home` | **AdGuard Home**, de gratis software die je zelf draait, bijvoorbeeld als app in Home Assistant of op een Raspberry Pi | Het adres, de gebruikersnaam en het wachtwoord van AdGuard Home |

Het dashboard volgt de taal van je Home Assistant: Nederlands als Home Assistant Nederlands is, anders Engels.

### AdGuard DNS (cloud) instellen

1. Maak een API-key aan op https://adguard-dns.io/en/dashboard/ : klik rechtsboven op je account, kies **User preferences** en ga naar **API keys**. Kopieer de key direct, je ziet hem maar één keer.
2. Vul op het tabblad **Configuratie** van deze app in:
   - `source`: `cloud`
   - `api_key`: je AdGuard DNS API-key
3. Start de app en open **AdGuard** in de zijbalk.

### AdGuard Home instellen

1. Vul op het tabblad **Configuratie** van deze app in:
   - `source`: `home`
   - `home_url`: het adres van de webinterface van AdGuard Home, bijvoorbeeld `http://192.168.1.2:3000`
   - `home_username` en `home_password`: de gebruikersnaam en het wachtwoord waarmee je inlogt bij AdGuard Home
2. Start de app en open **AdGuard** in de zijbalk.

Let goed op het wachtwoord: na 5 foute pogingen blokkeert AdGuard Home het inloggen 15 minuten.

**Gebruik je de AdGuard Home-app in Home Assistant?** Die app is normaal alleen via Home Assistant zelf bereikbaar. Open de AdGuard Home-app, ga naar het tabblad **Configuratie** en vul onder **Netwerk** een poort in bij **Web interface**, bijvoorbeeld `3000`. Start AdGuard Home opnieuw en vul bij `home_url` `http://<IP-adres van Home Assistant>:3000` in, met de gebruikersnaam en het wachtwoord van AdGuard Home.

Welke situatie heb jij? Draait AdGuard Home op een ander apparaat, zoals een Raspberry Pi, een NAS of een server, dan zet deze app het scherm in de zijbalk van Home Assistant. Draait AdGuard Home als app in Home Assistant zelf, dan heeft die app al een eigen knop in de zijbalk en heb je deze app daar niet voor nodig.

Zonder API-key of adres draait de app met voorbeeldgegevens.

### Overige instellingen

- `refresh_seconds`: hoe vaak het dashboard ververst (standaard 60 seconden)
- `home_country`: landcode van waar je woont, voor de speld op de wereldkaart (standaard `NL`, alleen bij AdGuard DNS)
- `allow_blocking`: of het dashboard iets mag wijzigen; zet uit voor een dashboard dat alleen mag kijken
- `allowed_users`: zie "Wie mag het dashboard zien" hieronder

### Wat je ziet

Met **AdGuard DNS**:

- **Dashboard**: verbruik van je abonnement, geblokkeerde verzoeken en bedrijven per uur met de verandering ten opzichte van de vorige periode, en je DNS-servers met hun apparaten.
- **Statistieken**: verzoeken per periode, soorten verzoeken, topbedrijven, topbestemmingen, topdomeinen en een wereldkaart. Filter op periode, server, apparaat en land.
- **Bedrijven**: het meest opgevraagde en meest geblokkeerde bedrijf en een tabel met de topbedrijven.
- **Querylog**: de laatste DNS-verzoeken, te doorzoeken en te filteren op geblokkeerd of toegestaan.

Met **AdGuard Home** zie je het originele scherm van AdGuard Home zelf, in de zijbalk van Home Assistant en automatisch ingelogd. Alles wat AdGuard Home biedt werkt zoals je gewend bent: dashboard, instellingen, filters, querylog en installatiegids. De taal volgt de instelling in AdGuard Home (onderaan de pagina).

### Blokkeren en deblokkeren (AdGuard DNS)

In de querylog staat achter elk verzoek een menu met drie puntjes. Kies **Domein blokkeren** of **Domein deblokkeren** en bevestig. Het dashboard voegt dan een gebruikersregel toe: `||domein^` om te blokkeren, `@@||domein^` om toe te staan. Via **Gebruikersregels** zie je alle regels en kun je ze weer verwijderen. Zet **Wijzigingen toestaan** uit als het dashboard alleen mag kijken.

### Instellingen wijzigen

- **AdGuard DNS**: via **Serverinstellingen** en **Blokkeerlijsten** bij je DNS-server, en **Instellingen** bij een apparaat, wijzig je alles in het dashboard zelf. Alleen een nieuw apparaat koppelen doe je op de site van AdGuard DNS.
- **AdGuard Home**: je wijzigt alles in het originele scherm van AdGuard Home, zoals je gewend bent. Met **Wijzigingen toestaan** uit kun je alleen kijken.

### Wie mag het dashboard zien

Alleen beheerders van Home Assistant zien de knop in de zijbalk. Heb je meerdere beheerders en mag niet iedereen het dashboard zien, vul dan op het tabblad **Configuratie** bij **Toegestane beheerders** de gebruikersnamen in die wel mogen. Laat je de lijst leeg, dan hebben alle beheerders toegang. Onderaan het dashboard staat "Ingelogd als" met je exacte gebruikersnaam.
