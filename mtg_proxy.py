#!/usr/bin/env python3
"""
MTG Proxy Printer - Fetch and format Magic: The Gathering cards for printing.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import List, Tuple, Optional

import requests
from PIL import Image
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

# Constants
SCRYFALL_API = "https://api.scryfall.com"
CARD_WIDTH_INCHES = 2.5
CARD_HEIGHT_INCHES = 3.5
DEFAULT_DPI = 300
CARDS_PER_ROW = 3
CARDS_PER_COL = 3
CARDS_PER_PAGE = CARDS_PER_ROW * CARDS_PER_COL

# Cache directory
CACHE_DIR = Path(__file__).parent / "cache"


def setup_cache():
    """Create cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(exist_ok=True)


def sanitize_filename(name: str) -> str:
    """Convert card name to safe filename."""
    return re.sub(r'[<>:"/\\|?*]', '_', name.lower().replace(' ', '_'))


def parse_card_entry(entry: str) -> Tuple[str, int]:
    """Parse a card entry like '4x Lightning Bolt' into (name, quantity)."""
    entry = entry.strip()
    if not entry or entry.startswith('#'):
        return None, 0
    
    # Match patterns like "4x Card Name", "4 Card Name", or just "Card Name"
    match = re.match(r'^(\d+)x?\s+(.+)$', entry, re.IGNORECASE)
    if match:
        quantity = int(match.group(1))
        name = match.group(2).strip()
    else:
        quantity = 1
        name = entry
    
    return name, quantity


def fetch_card_data(card_name: str) -> Optional[dict]:
    """Fetch card data from Scryfall API."""
    url = f"{SCRYFALL_API}/cards/named"
    params = {"fuzzy": card_name}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError as e:
        if response.status_code == 404:
            print(f"  ⚠ Card not found: {card_name}")
        else:
            print(f"  ⚠ API error for '{card_name}': {e}")
        return None
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Network error for '{card_name}': {e}")
        return None


def get_image_url(card_data: dict) -> Optional[str]:
    """Extract the best image URL from card data."""
    if 'image_uris' in card_data:
        # Prefer 'large' or 'png' for best quality
        for key in ['png', 'large', 'normal']:
            if key in card_data['image_uris']:
                return card_data['image_uris'][key]
    
    # Handle double-faced cards
    if 'card_faces' in card_data and len(card_data['card_faces']) > 0:
        face = card_data['card_faces'][0]
        if 'image_uris' in face:
            for key in ['png', 'large', 'normal']:
                if key in face['image_uris']:
                    return face['image_uris'][key]
    
    return None


def download_image(url: str, card_name: str, use_cache: bool = True) -> Optional[Path]:
    """Download card image and return local path."""
    filename = sanitize_filename(card_name) + ".png"
    cache_path = CACHE_DIR / filename
    
    # Check cache first
    if use_cache and cache_path.exists():
        return cache_path
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        with open(cache_path, 'wb') as f:
            f.write(response.content)
        
        return cache_path
    except requests.exceptions.RequestException as e:
        print(f"  ⚠ Failed to download image for '{card_name}': {e}")
        return None


def resize_card_image(image_path: Path, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Resize card image to standard MTG dimensions at specified DPI."""
    target_width = int(CARD_WIDTH_INCHES * dpi)
    target_height = int(CARD_HEIGHT_INCHES * dpi)
    
    img = Image.open(image_path)
    
    # Convert to RGB if necessary (for PNG with transparency)
    if img.mode in ('RGBA', 'P'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    # Resize maintaining aspect ratio, then crop/pad to exact size
    img_ratio = img.width / img.height
    target_ratio = target_width / target_height
    
    if img_ratio > target_ratio:
        # Image is wider than target
        new_height = target_height
        new_width = int(new_height * img_ratio)
    else:
        # Image is taller than target
        new_width = target_width
        new_height = int(new_width / img_ratio)
    
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    # Center crop to exact dimensions
    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    img = img.crop((left, top, left + target_width, top + target_height))
    
    return img


def create_pdf(cards: List[Tuple[str, Image.Image]], output_path: str, dpi: int = DEFAULT_DPI):
    """Create PDF with cards arranged in a grid."""
    page_width, page_height = LETTER
    
    # Calculate card dimensions in points (72 points per inch)
    card_width_pts = CARD_WIDTH_INCHES * 72
    card_height_pts = CARD_HEIGHT_INCHES * 72
    
    # Calculate margins to center the grid
    grid_width = CARDS_PER_ROW * card_width_pts
    grid_height = CARDS_PER_COL * card_height_pts
    margin_x = (page_width - grid_width) / 2
    margin_y = (page_height - grid_height) / 2
    
    c = canvas.Canvas(output_path, pagesize=LETTER)
    
    card_index = 0
    total_cards = len(cards)
    
    while card_index < total_cards:
        # Draw cards for this page
        for row in range(CARDS_PER_COL):
            for col in range(CARDS_PER_ROW):
                if card_index >= total_cards:
                    break
                
                card_name, card_img = cards[card_index]
                
                # Calculate position (bottom-left origin in PDF)
                x = margin_x + col * card_width_pts
                y = page_height - margin_y - (row + 1) * card_height_pts
                
                # Save image temporarily for PDF embedding
                temp_path = CACHE_DIR / f"temp_{card_index}.jpg"
                card_img.save(temp_path, "JPEG", quality=95)
                
                c.drawImage(str(temp_path), x, y, 
                           width=card_width_pts, height=card_height_pts)
                
                # Clean up temp file
                temp_path.unlink()
                
                card_index += 1
        
        # Add new page if more cards remain
        if card_index < total_cards:
            c.showPage()
    
    c.save()


def load_decklist(filepath: str) -> List[Tuple[str, int]]:
    """Load card list from file."""
    cards = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                name, qty = parse_card_entry(line)
                if name:
                    cards.append((name, qty))
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    except Exception as e:
        print(f"Error reading file: {e}")
        sys.exit(1)
    
    return cards


def main():
    parser = argparse.ArgumentParser(
        description="Fetch MTG card images and create printable proxy sheets."
    )
    parser.add_argument(
        'cards', 
        nargs='*', 
        help="Card names (use quotes for multi-word names)"
    )
    parser.add_argument(
        '-f', '--file',
        help="Read card names from a text file"
    )
    parser.add_argument(
        '-o', '--output',
        default='proxies.pdf',
        help="Output PDF filename (default: proxies.pdf)"
    )
    parser.add_argument(
        '--dpi',
        type=int,
        default=DEFAULT_DPI,
        help=f"Print DPI (default: {DEFAULT_DPI})"
    )
    parser.add_argument(
        '--no-cache',
        action='store_true',
        help="Don't use cached images"
    )
    
    args = parser.parse_args()
    
    # Collect card entries
    card_entries = []
    
    if args.file:
        card_entries.extend(load_decklist(args.file))
    
    for card in args.cards:
        name, qty = parse_card_entry(card)
        if name:
            card_entries.append((name, qty))
    
    if not card_entries:
        print("No cards specified. Use --help for usage information.")
        sys.exit(1)
    
    # Setup
    setup_cache()
    use_cache = not args.no_cache
    
    # Process cards
    print(f"Processing {sum(qty for _, qty in card_entries)} cards...")
    processed_cards = []
    
    for card_name, quantity in card_entries:
        print(f"  Fetching: {card_name} (x{quantity})")
        
        # Respect Scryfall rate limit (100ms between requests)
        time.sleep(0.1)
        
        card_data = fetch_card_data(card_name)
        if not card_data:
            continue
        
        actual_name = card_data.get('name', card_name)
        image_url = get_image_url(card_data)
        
        if not image_url:
            print(f"  ⚠ No image available for: {actual_name}")
            continue
        
        image_path = download_image(image_url, actual_name, use_cache)
        if not image_path:
            continue
        
        # Resize and add to list (repeated for quantity)
        resized_img = resize_card_image(image_path, args.dpi)
        for _ in range(quantity):
            processed_cards.append((actual_name, resized_img))
        
        print(f"  ✓ {actual_name}")
    
    if not processed_cards:
        print("No cards were successfully processed.")
        sys.exit(1)
    
    # Generate PDF
    print(f"\nGenerating PDF: {args.output}")
    create_pdf(processed_cards, args.output, args.dpi)
    
    pages = (len(processed_cards) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE
    print(f"✓ Created {args.output} with {len(processed_cards)} cards on {pages} page(s)")
    print(f"  Print at 100% scale, no margins, for correct card size.")


if __name__ == "__main__":
    main()
