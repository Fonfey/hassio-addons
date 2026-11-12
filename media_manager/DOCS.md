# Media manager

[English](#english) · [Nederlands](#nederlands)

## English

The media browser in Home Assistant shows a grey icon and a cut-off name for every file in "My media", so you cannot see which photo is which. This app shows the media folder with real thumbnails, the full file name and the date the photo was taken.

The app follows the language of your Home Assistant: Dutch if Home Assistant is set to Dutch, English otherwise.

### What you can do

- **Browse** the media folder and its subfolders, grouped by month or year. Filter by year or by kind (photos, videos, audio) and search by name or date.
- **View** a photo, video or audio file large, with the date, size and dimensions. Use the arrow keys or swipe for the next one.
- **Upload** by dragging files onto the page or with the Upload button. Large videos are sent in parts, so this also works through Nabu Casa.
- **Rename, move, download and delete**, one file or a selection. Several files download as one zip file.
- **Copy the path** to use a file in Home Assistant: the `media-source://` address for media players and picture cards, and the `/media/...` path for automations.

### Trash

Deleted files go to the trash first. You can restore them for 30 days (adjustable), after that they are removed for good. The trash is not included in backups. Set **Keep trash** to 0 to delete right away.

### Good to know

- The date comes from the file name (for example `20250815_145843.jpg`), from the photo itself (EXIF), or otherwise from when the file was last changed. When you rename a photo, its date is kept.
- Thumbnails are made once per file in the background and are not included in backups.
- The media folder itself is included in every Home Assistant backup, so deleting photos you no longer need also makes your backups smaller.
- HEIC photos from an iPhone get no thumbnail; download them to view them.

### Settings

- **Folder**: which folder the app shows. `/media` by default, the folder behind "My media" in Home Assistant. You can also choose a folder inside it, such as `/media/photos`, or a folder in `/share`, for example one you added to Home Assistant with `media_dirs`. If the folder does not exist, the app says so and shows `/media`. The `media-source://` address is only shown for files in `/media`.
- **Keep trash (days)**: 30 by default. 0 deletes right away.
- **Allowed administrators (optional)**: only administrators see the sidebar item; enter usernames if not every administrator may use Media manager.

### Local

The app runs completely locally. It only reads and writes the chosen folder of your own Home Assistant and loads nothing from the internet, not even fonts.

## Nederlands

De mediabrowser van Home Assistant toont in "My media" bij elk bestand een grijs icoon en een afgekapte naam, zodat je niet ziet welke foto welke is. Deze app toont de mediamap met echte miniaturen, de volledige bestandsnaam en de datum waarop de foto is gemaakt.

### Wat je kunt doen

- **Bladeren** door de mediamap en de mappen daarin, gegroepeerd per maand of jaar. Filter op jaar of op soort (foto's, video's, audio) en zoek op naam of datum.
- **Bekijken**: een foto, video of audiobestand groot, met datum, grootte en afmetingen. Met de pijltjestoetsen of door te vegen ga je naar de volgende.
- **Uploaden** door bestanden op de pagina te slepen of met de knop Uploaden. Grote video's gaan in stukken, zodat het ook via Nabu Casa werkt.
- **Hernoemen, verplaatsen, downloaden en verwijderen**, één bestand of een selectie. Meerdere bestanden download je als één zip-bestand.
- **Het pad kopiëren** om een bestand in Home Assistant te gebruiken: het `media-source://`-adres voor mediaspelers en afbeeldingskaarten, en het pad `/media/...` voor automations.

### Prullenbak

Verwijderde bestanden gaan eerst naar de prullenbak. Daar kun je ze 30 dagen terugzetten (instelbaar), daarna gaan ze definitief weg. De prullenbak gaat niet mee in backups. Zet **Prullenbak bewaren** op 0 om meteen definitief te verwijderen.

### Goed om te weten

- De datum komt uit de bestandsnaam (bijvoorbeeld `20250815_145843.jpg`), uit de foto zelf (EXIF), of anders van de laatste wijziging. Als je een foto hernoemt, blijft de datum bewaard.
- Miniaturen worden één keer per bestand op de achtergrond gemaakt en gaan niet mee in backups.
- De mediamap zelf gaat wel mee in elke backup van Home Assistant. Foto's opruimen die je niet meer nodig hebt, maakt je backups dus ook kleiner.
- HEIC-foto's van een iPhone krijgen geen miniatuur; download ze om ze te bekijken.

### Instellingen

- **Map**: welke map de app toont. Standaard `/media`, de map achter "Mijn media" in Home Assistant. Je kunt ook een map daarin kiezen, zoals `/media/fotos`, of een map in `/share`, bijvoorbeeld een die je met `media_dirs` aan Home Assistant hebt toegevoegd. Bestaat de map niet, dan meldt de app dat en toont hij `/media`. Het `media-source://`-adres staat er alleen bij voor bestanden in `/media`.
- **Prullenbak bewaren (dagen)**: standaard 30. 0 is meteen definitief verwijderen.
- **Toegestane beheerders (optioneel)**: alleen beheerders zien de knop in de zijbalk; vul gebruikersnamen in als niet elke beheerder Media manager mag gebruiken.

### Lokaal

De app draait volledig lokaal. Hij leest en schrijft alleen de gekozen map van je eigen Home Assistant en laadt niets van internet, ook geen lettertypen.
