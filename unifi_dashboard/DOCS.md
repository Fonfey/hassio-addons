# UniFi Dashboard

Live overzicht van je UniFi-netwerk: WAN-verkeer, netwerkapparaten en clients.

## Instellen

1. Open UniFi Network (het IP-adres van je console in de browser), ga naar **Instellingen → Control Plane → Integraties** en klik op **Create API Key**. Kopieer de key direct, je ziet hem maar één keer.
2. Vul op het tabblad **Configuratie** van deze app in:
   - `unifi_host`: IP-adres of hostnaam van je UniFi-console (bijv. `192.168.1.1`)
   - `unifi_api_key`: de API-key
   - `unifi_site`: optioneel, naam van de site (leeg = eerste site)
   - `refresh_seconds`: hoe vaak de gegevens ververst worden
3. Start de app en open **UniFi** in de zijbalk.

Zonder API-key draait de app in demomodus met voorbeeldgegevens.

## Wat je ziet

- **Netwerkkaart**: je opstelling van internet via de gateway naar switches en toegangspunten. Bewegende lijnen tonen waar verkeer loopt; klik op een apparaat voor de details.
- **Internetverkeer**: download en upload over 30 minuten, 6 uur of 24 uur. De geschiedenis blijft bewaard na een herstart.
- **Apparaten**: switchpoorten als frontpaneel (snelheid en PoE), wifi-radio's per toegangspunt (band, kanaal, breedte), uptime, firmware, processor en geheugen.
- **Clients**: zoeken, filteren op wifi of kabel en op het apparaat waarmee ze verbonden zijn. Nieuwe clients van het afgelopen uur krijgen een label.

## Wie mag het dashboard zien

Alleen beheerders van Home Assistant zien de knop in de zijbalk. Heb je meerdere beheerders en mag niet iedereen het dashboard zien, vul dan op het tabblad **Configuratie** bij **Toegestane beheerders** de gebruikersnamen in die wel mogen. Laat je de lijst leeg, dan hebben alle beheerders toegang. Onderaan het dashboard staat "Ingelogd als" met je exacte gebruikersnaam.
