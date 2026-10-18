# Bringing in other sources

How to get data Towpath can't reach through a provider's API onto your server, and what Towpath does with it.
Each one ends in a folder on the server or a connection on the Connections page.

## Facebook

1. On Facebook, open **Download your information** and request a copy in **JSON** format, all dates, with
   messages and posts at least. Photos can be left out if you keep originals elsewhere.
2. Unzip it into a folder under the server's library (for example `exports/facebook-2019`).
3. In Towpath, **Connections → Facebook export**, choose that folder, then **Start indexing**.

Towpath reads your posts (one item each, with check-in places) and your messages (one item per conversation per
day), fixing the way Facebook stores accented letters and emoji. They become searchable, and with the Life page's
permission the life story reads them, counting your own posts and the conversations you wrote in as likely
evidence. The export itself is never changed.

## Apple

Apple offers no general data API, so these are copies you make, then index like any folder.

- **iCloud Mail:** *Connections → iCloud Mail*, with your iCloud address and an app-specific password from
  account.apple.com. Mail management works on it as on Fastmail (dry runs first). Yahoo Mail works the same way
  (*Connections → Yahoo Mail*). Outlook.com isn't offered: Microsoft no longer accepts passwords over IMAP for
  personal accounts.
- **iCloud Photos:** copy them to the server with [icloudpd](https://github.com/icloud-photos-downloader/icloud_photos_downloader)
  (it needs *Access iCloud Data on the Web* turned on and a sign-in every few months), then add the folder under
  *Folders on this server*. The Photos page then reads their cameras, dates and places.
- **Messages:** there is no API. Export them on a Mac with
  [imessage-exporter](https://github.com/ReagentX/imessage-exporter) in its **text** format into a folder on the
  server, then *Connections → Apple Messages export*. Each conversation becomes one item per day, searchable from
  Search all and shown on People pages, with the same protections as mail.
- **Contacts and calendars:** *Connections → iCloud contacts and calendars*, with your Apple Account email and an
  app-specific password. Towpath finds the address books and calendars the standard way and only reads them.

## Google

- **Gmail:** either Towpath's read-only Gmail connection (Google sign-in), or *Gmail (IMAP)* with app passwords,
  which mail management also uses.
- **Takeout:** unpack it into a folder on the server and index it as folders; mail in it (`.mbox`) is searchable
  through the folder index. A dedicated Takeout importer is not built yet.
