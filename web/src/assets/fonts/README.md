# Numeric typeface

The application self-hosts the unmodified Roboto variable Latin WOFF2 from Google Fonts.
The CSS face is restricted to digits, numeric separators and currency symbols so Chinese
and other UI text retain the system font. Tabular, lining figures are enabled in the
face and inherited by the app; Canvas price-action labels use the same family.

- Source family: https://fonts.google.com/specimen/Roboto
- File: https://fonts.gstatic.com/s/roboto/v51/KFO7CnqEu92Fr1ME7kSn66aGLdTylUAMa3yUBHMdazQ.woff2
- Upstream: https://github.com/googlefonts/roboto-3-classic
- License: SIL Open Font License 1.1, distributed at `web/public/assets/licenses/Roboto-OFL.txt`.

Vite fingerprints the font asset and its preload URL. No external font request is made
by the deployed application.
