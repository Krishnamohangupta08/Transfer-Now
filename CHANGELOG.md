# Changelog — Transfer Now

## 1.0.0.1 (2026-11-24)

Summary: This update makes the app smoother and easier to use. You can now see
the full release history inside the app, save a QR code as a picture, and send
more than 20 files at once (we pack them into one ZIP for you).

### New Features
- **Changelog page** — a new "Changelog" button in the sidebar, just above
  Settings. It shows what changed in every version, newest first. Click "Expand
  all" to open every version, or "Collapse all" to close them.
- **Save or copy your QR code** — the "Scan to open the link" window now lets you
  save the QR code as a picture (PNG) or copy it, so you can paste it into a chat
  or document. You can pick the picture size (small to very large) and choose a
  white or see-through background. The QR code is made on your computer; nothing
  is sent to any website.
- **Up to 20 items per transfer** — the Upload page now shows a counter like
  "Files selected: 12 / 20" so you always know how many more you can add. When
  you reach 20, the Add buttons switch off and a short message tells you why.
- **Automatic ZIP for big selections** — picked more than 20 files? Transfer Now
  tells you about the limit and offers "Create ZIP and Upload". It puts all your
  files into one ZIP and sends it as a single item. Your original files are never
  changed, and you can cancel while the ZIP is being made.

### Improvements
- **Smooth scrolling on every page** — scrolling with the mouse wheel or
  touchpad now glides softly instead of jumping.
- **Neater scrollbar** — the scrollbar is slimmer, sits right at the edge of the
  window, and gets darker when you point at it. The History page now shows the
  same scrollbar as all other pages.
- **Clearer Theme menu** — each theme in View > Theme now has a small round picture of its
  colours (and System has a screen icon). The theme you are using is softly highlighted.
- **Reset everything** — Settings > Data now has a "Reset everything" button. It erases your
  history, saved transfers, settings and other app data, then restarts the app like a fresh
  install. You are asked to confirm first, and your uploaded and downloaded files are never
  touched.
- **No double files** — if you add the same file twice, the app skips it, and the
  file counter updates correctly when you remove files.
- **Smoother ZIP making** — creating a ZIP never freezes the app. If you cancel or
  close the app, half-made ZIP files are cleaned up. After your transfer
  finishes, the temporary ZIP is deleted automatically.
- **Limit cannot be skipped** — the 20-item limit is checked behind the scenes
  too, so it always applies.
- **Easier to share the app** — building the app now always creates a ready-to-share
  ZIP file.

### Bug Fixes
- **Correct version number everywhere** — the About window, the app file details
  and the documents now all show 1.0.0.1.
- **Changelog works in the installed app** — the installed version of Transfer
  Now now includes this changelog and its pictures, so the Changelog page looks
  complete there too.

### Breaking Changes
- **20 items per transfer** — one transfer can no longer hold more than 20 items.
  If you pick more, let Transfer Now put them in one ZIP for you, or send them in
  two or more transfers.

## 1.0.0.0

Summary: Welcome to Transfer Now — a fast desktop app for sharing files with
TransferNow. It is free, needs no account, lets you send up to 5 GB per transfer,
and has no limit on how many files you send.

### New Features
- **Share a link in one click** — send up to 5 GB per transfer, for free, with no
  account. Your link works for 7 days.
- **Folders just work** — choose or drop a whole folder and every file inside it
  (including subfolders) is added for you. Nothing is zipped.
- **Drag and drop anywhere** — drop files onto the window and watch the drop area
  light up, or use the "Browse files" and "Add folder" buttons.
- **Sender email** — we make a new random email address for each transfer, or you
  can type your own.
- **Schedule uploads** — send right now or pick a time. If a transfer fails, the
  app tries again by itself.
- **Transfer queue** — send many transfers at once (1 to 4 at the same time),
  change their order, retry or cancel them, and copy all links in one click.
- **Live progress** — see the real percentage, a speed graph and the time left for
  every transfer.
- **Download from a link** — paste a TransferNow link, look at the files inside,
  pick a folder and save them. You can pause a download, and it carries on if your
  internet drops.
- **History** — see every upload and download. Search, filter, or save the list
  as a CSV file.
- **Link health check** — one click shows which of your saved links still work.
- **QR codes** — make a QR code for any link, so someone can open it with a
  phone.
- **Dashboard** — see your current speed, your totals, your activity this week
  and how healthy your links are.
- **Clipboard helper** — if you copy a TransferNow link, the app offers to open it.
- **Tray icon and notifications** — keep the app running in the background and get
  a message when a transfer is done.
- **Quick commands** — press `Ctrl+K` to search for any action. Press `F1` to see
  all shortcuts, for example `Ctrl+Enter` to get a link, `Ctrl+Shift+V` to paste
  and fetch a link, `Ctrl+B` to hide the sidebar and `F11` for full screen.
- **Collapsible sidebar** — shrink the sidebar with a smooth animation to get more
  room.

### Improvements
- **Themes** — choose dark, light, or follow your computer. There are five looks
  (Graphite, Graphite Black, Graphite Blue, Porcelain and Porcelain Warm) and 7
  accent colours.
- **8 languages** — English, Hindi, Spanish, French, German, Portuguese (Brazil),
  Japanese and Chinese.
- **Settings you can change** — looks and language, notifications (where they
  appear, how long, sound), sound effects and volume, transfers (sender email,
  how many at once, copy link automatically, a natural sending speed, retry on
  failure) and downloads (where files are saved).
- **Your data stays after updates** — settings and history are kept in your user
  folder, so updating the app never erases them.
- **Works on Windows, macOS and Linux**, and can be packed as a standalone app.
