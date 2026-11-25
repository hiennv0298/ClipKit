# Changelog

Each release's section becomes its notes on the GitHub Releases page and in the app's "What's new" window.
Add a `## <version>` section here before pushing a `v<version>` tag.

## 0.3.1

- **New app icon** for the Start menu, the desktop shortcut, the installer and the programs themselves.
- The app window's taskbar icon is now sharp instead of blurry.

## 0.3.0

- **Updates inside the app.** ClipKit checks GitHub for a new version once a day and shows a notice with the release notes. With the installer version, **Update now** downloads the new installer, checks its SHA-256 fingerprint, installs it and reopens ClipKit.
- **What's new** window after an update, and a version button in the header to check for updates by hand.
- Turn update checks off under *More options* if you prefer ClipKit not to go online for this.
- **Preview a moment before rendering:** press ▶ on a card to play that part of the original video right there.
- Rendered clips move to the top of the list as soon as each one is ready.
- More space between the progress panel and the moment cards.

## 0.2.0

- New app window: paste a link, review the moments as cards (untick, change times or title), render, and play the clips right in the app.
- Separate `ClipKitApp.exe` (app window) and `clipkit.exe` (command line).
- Everything in English.
- Clips follow YouTube's recommended encoding, keep the source frame rate, and keep captions and title out of the areas covered by the Shorts interface.
- Stop cancels downloading, transcribing and rendering.

## 0.1.0

- First release: download with yt-dlp, transcribe with Whisper, pick moments with a local scoring algorithm, render captioned 9:16 clips with FFmpeg.
