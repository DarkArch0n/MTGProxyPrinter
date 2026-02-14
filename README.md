# MTG Proxy Printer

A Python tool to fetch Magic: The Gathering card images and format them for printing proxies.

## Features

- Fetch high-quality card images from Scryfall API
- Resize cards to standard MTG size (2.5" x 3.5")
- Generate print-ready PDF sheets (3x3 grid per page)
- Support for card lists via text file input
- Automatic image caching to avoid re-downloading

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### Basic Usage

```bash
# Single card
python mtg_proxy.py "Lightning Bolt"

# Multiple cards
python mtg_proxy.py "Lightning Bolt" "Counterspell" "Dark Ritual"

# From a text file (one card name per line)
python mtg_proxy.py --file decklist.txt

# Specify quantities
python mtg_proxy.py "4x Lightning Bolt" "2x Counterspell"
```

### Options

```bash
--file, -f       Read card names from a text file
--output, -o     Output PDF filename (default: proxies.pdf)
--dpi            Print DPI (default: 300)
--no-cache       Don't use cached images
```

### Decklist Format

Create a text file with card names, optionally with quantities:

```
4x Lightning Bolt
2x Counterspell
1x Black Lotus
Sol Ring
```

## Output

The tool generates a PDF with cards arranged in a 3x3 grid, sized for standard letter paper (8.5" x 11"). Cut along the grid lines for game-sized proxies.

## Legal Notice

This tool is for personal, non-commercial use only. Magic: The Gathering is a trademark of Wizards of the Coast. Card images are fetched from Scryfall, which provides them under fair use for personal reference. Do not sell proxies made with this tool.

## License

MIT License - See LICENSE file
