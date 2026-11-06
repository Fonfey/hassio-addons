# Changelog

## 1.4.3

### Verbeterd
- Nieuwe, ruimtelijke tekeningen van de apparaten die meer op de echte hardware lijken: de Cloud Gateway als staand blok met schermpje, desktop- en compacte switches met hun poorten aan de voorkant, rackapparaten met oren, ronde en vierkante plafond-AP's (zoals de U7 Pro). De tekeningen zijn ook groter.

## 1.4.2

### Verbeterd
- Apparaten hebben nu een eigen tekening die op het model lijkt: een ronde schijf voor plafond-AP's, een staande behuizing voor mesh- en buiten-AP's, een compacte gateway zoals de Cloud Gateway, en desktop- of rackswitches. Het lampje in de tekening toont de status: blauw is online, rood is offline.
- Clients tonen een wifi- of kabelpictogram.

## 1.4.1

### Verbeterd
- De pagina van de gateway (bijvoorbeeld de Dream Machine) toont nu ook de poorten en wat erop is aangesloten, naast download, upload en belasting.
- De poort naar je provider staat in de lijst als **Internet (WAN)**.

## 1.4.0

### Nieuw
- **Wat zit op welke poort**: per switchpoort zie je welk apparaat of welke client erop is aangesloten, inclusief de uplink naar de switch erboven. Klik op een poort om hem op te zoeken.
- **Doorklikken**: tabbladen Overzicht, Apparaten en Clients in plaats van één lange pagina. Elk apparaat en elke client heeft een eigen pagina met de gegevens, de poort en wat er nog meer op hetzelfde apparaat zit.
- In de clientlijst en bij apparaten staat nu ook het poortnummer, bijvoorbeeld "Switch meterkast, poort 10".

## 1.3.0

### Gewijzigd
- De app wordt nu als kant-en-klaar image geleverd. Installeren en bijwerken gaat daardoor sneller, want Home Assistant hoeft niets meer zelf te bouwen.
- Installeren gaat voortaan via de repository Fonfey/hassio-addons, samen met de andere apps.

## 1.2.5

### Opgelost
- Scrollbalken hebben nu de kleuren van het dashboard in plaats van witte Windows-scrollbalken.

## 1.2.4

### Gewijzigd
- De knop in de zijbalk is weer alleen zichtbaar voor beheerders. Gewone gebruikers zien hem niet meer.
- **Toegestane gebruikers** heet nu **Toegestane beheerders (optioneel)**. Laat de lijst leeg om alle beheerders toegang te geven, of vul namen in als niet elke beheerder het dashboard mag zien.

## 1.2.3

### Verbeterd
- Onderaan het dashboard staat nu "Ingelogd als" met je exacte gebruikersnaam, zodat je die foutloos kunt overnemen bij Toegestane gebruikers.
- De melding "Geen toegang" toont met welke gebruikersnaam iemand probeerde.
- Het logboek van de app toont bij het starten wie toegang heeft, en meldt geweigerde gebruikers met hun exacte naam.
- De uitleg bij Toegestane gebruikers vertelt waar je de gebruikersnamen vindt.

## 1.2.2

### Verbeterd
- Nieuw, rustiger icoon en logo: een eenvoudig wit netwerksymbool op een blauwe achtergrond.

## 1.2.1

### Verbeterd
- Eigen icoon en logo in de App Store.
- De instellingen en de beschrijving leggen uit waar je de API-key aanmaakt.

## 1.2.0

### Nieuw
- De knop in de zijbalk is nu zichtbaar voor alle gebruikers, niet alleen voor beheerders.
- Nieuwe instelling **Toegestane gebruikers**: vul Home Assistant-gebruikersnamen in om het dashboard alleen voor hen open te zetten. Laat de lijst leeg om iedereen toegang te geven. Anderen zien de melding "Geen toegang".
- De instellingen op het tabblad Configuratie hebben nu een Nederlandse naam en uitleg.

## 1.1.3

### Opgelost
- Een uitgeschakelde wifi-radio (bijvoorbeeld 6 GHz) staat nu als "Uit" in plaats van "Kanaal 0" met 0% herhaald.
- De as van de verkeersgrafiek gebruikt nette, ronde stappen. Bij weinig verkeer staat er niet meer twee keer dezelfde waarde, en kleine waarden staan in kbit/s.

## 1.1.2

### Opgelost
- De verbindingslijnen in de netwerkkaart zijn op een telefoon nu doorgetrokken lijnen in plaats van rommelige stippels. Kleur en dikte laten nog steeds zien hoeveel verkeer er loopt.

## 1.1.1

### Verbeterd
- Aparte weergave voor telefoons: switchpoorten lopen door over meerdere regels in plaats van buiten beeld te vallen.
- Clients staan op een telefoon als overzichtelijke lijst met naam, verbinding en IP-adres, in plaats van een brede tabel.
- Wifi-radio's nemen op een telefoon één regel per band in.
- Uptime, firmware, processor en geheugen staan op een telefoon naast elkaar.
- Minder witruimte aan de randen op kleine schermen.

### Opgelost
- De link "Ga naar UniFi Dashboard voor meer informatie" in Home Assistant opent nu de GitHub-repository.

## 1.1.0

### Nieuw
- Netwerkkaart met je opstelling van internet via de gateway naar switches en toegangspunten. Bewegende lijnen tonen waar verkeer loopt; klik op een apparaat om naar de details te gaan.
- Switchpoorten als frontpaneel: kleur per snelheid, PoE-indicator en SFP-poorten apart.
- Wifi-radio's per toegangspunt met band, kanaal, kanaalbreedte en percentage herhaalde pakketten.
- Internetverkeer over 30 minuten, 6 uur of 24 uur, met gemiddelde, piek en waarden onder je muis. De geschiedenis blijft bewaard na een herstart.
- Clients filteren op het apparaat waarmee ze verbonden zijn; nieuwe clients van het afgelopen uur krijgen een label.

### Verbeterd
- Nieuw ontwerp dat licht of donker volgt met je systeeminstelling.
- Statusregel bovenaan vat in één zin samen hoe je netwerk ervoor staat.
- Op een telefoon wordt de netwerkkaart een verticale boom.

## 1.0.0

- Eerste versie: WAN-verkeer, netwerkapparaten en clients via de UniFi Network API.
- Instellingen via het tabblad Configuratie; dashboard via de zijbalk.
