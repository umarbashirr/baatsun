# Baatsun logo

![Baatsun](baatsun-lockup.svg)

**The mark** is the Devanagari letter **ब** ("ba", for *baat*: talk) with its
headstroke replaced by the Baatsun pill.

In Devanagari, every letter hangs from a horizontal headstroke called the
*shirorekha*. Baatsun's on-screen pill is also a horizontal bar. So the
letter hangs from the product: the mark names the app, keeps its Hindi roots,
and shows the one object everyone sees while dictating.

## Files

| File | Use |
|---|---|
| `baatsun.svg` | App icon, with a margin, for icon themes (installed as `hicolor/scalable/apps/baatsun.svg`) |
| `baatsun-tile.svg` | The tile without a margin: favicons, the README, in-app use |
| `baatsun-lockup.svg` | Mark and wordmark together; the wordmark is outlined, so no font is needed |
| `png/baatsun-{16…512}.png` | Rendered app icon sizes (installed into `hicolor/NxN/apps/baatsun.png`) |
| `png/baatsun-tile-180.png` | Apple touch icon for the website |

## Colours

| | |
|---|---|
| Tile | deep green `#1f3d2e` |
| Letter and pill | cream `#faf8f3` |
| Wordmark | ink `#1b1a17` |

These are the same tokens the app window and the website use.

## Rules

- Keep the letter and the pill the same colour on the tile. The pill is
  part of the letter, not a separate accent.
- Don't put a microphone or waveform with it: the pill is the voice.
- Leave clear space around the tile of at least a quarter of its width.
- Below 16 px, use the tile on its own without the wordmark.

## Rebuilding

`build.py` regenerates every file here from the two fonts (instructions at
the top of the script). The copies in `docs/assets/`, `electron/src/main/icon.png`
and `electron/src/renderer/assets/logo.svg` come from these files.

## Construction and licences

The letter is the ब glyph from **Mukta ExtraBold** (Ek Type, SIL Open Font
License 1.1). Everything above its headstroke is clipped away, and a rounded
bar (the pill) is set slightly above it, wider than the letter.

The wordmark is "baatsun" in **Tiro Devanagari Hindi** (Tiro Typeworks, SIL
OFL 1.1), converted to outlines. The OFL allows fonts to be used in logos and
artwork. Both licence texts are in `docs/fonts/`.
