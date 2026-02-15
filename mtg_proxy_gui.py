#!/usr/bin/env python3
"""
MTG Proxy Printer GUI - Visual interface for creating proxy card sheets.
"""

import os
import re
import sys
import time
import threading
from pathlib import Path
from typing import List, Tuple, Optional
from io import BytesIO

import requests
from PIL import Image, ImageTk
from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Constants
SCRYFALL_API = "https://api.scryfall.com"
CARD_WIDTH_INCHES = 2.5
CARD_HEIGHT_INCHES = 3.5
DEFAULT_DPI = 300
CARDS_PER_ROW = 3
CARDS_PER_COL = 3
CARDS_PER_PAGE = CARDS_PER_ROW * CARDS_PER_COL

# Preview sizing
PREVIEW_CARD_WIDTH = 120
PREVIEW_CARD_HEIGHT = int(PREVIEW_CARD_WIDTH * (CARD_HEIGHT_INCHES / CARD_WIDTH_INCHES))

# Cache directory - works both as script and as frozen exe
if getattr(sys, 'frozen', False):
    BASE_DIR = Path(sys.executable).parent
else:
    BASE_DIR = Path(__file__).parent
CACHE_DIR = BASE_DIR / "cache"


def setup_cache():
    """Create cache directory if it doesn't exist."""
    CACHE_DIR.mkdir(exist_ok=True)


def sanitize_filename(name: str) -> str:
    """Convert card name to safe filename."""
    return re.sub(r'[<>:"/\\|?*]', '_', name.lower().replace(' ', '_'))


# Moxfield section headers to skip
MOXFIELD_SECTIONS = {
    'deck', 'sideboard', 'commander', 'companions', 'maybeboard',
    'mainboard', 'considering', 'acquired',
}


def parse_card_entry(entry: str) -> Tuple[Optional[str], int, Optional[str], Optional[str]]:
    """Parse a card entry into (name, quantity, set_code, collector_number).
    
    Supports formats:
      - Simple:    4x Lightning Bolt
      - Moxfield:  1 Lightning Bolt (2X2) 117
      - Moxfield:  1 Lightning Bolt (2X2) 117 *F*
    """
    entry = entry.strip()
    if not entry or entry.startswith('#'):
        return None, 0, None, None
    
    # Skip Moxfield section headers like "Deck", "Sideboard", etc.
    if entry.lower().rstrip(':') in MOXFIELD_SECTIONS:
        return None, 0, None, None
    
    # Try Moxfield format: qty Card Name (SET) CollectorNum [*F*]
    mox_match = re.match(
        r'^(\d+)x?\s+(.+?)\s+\(([A-Za-z0-9]+)\)\s+(\S+)(?:\s+\*[A-Z]+\*)?\s*$',
        entry, re.IGNORECASE
    )
    if mox_match:
        quantity = int(mox_match.group(1))
        name = mox_match.group(2).strip()
        set_code = mox_match.group(3).strip().lower()
        collector_num = mox_match.group(4).strip()
        return name, quantity, set_code, collector_num
    
    # Simple format: qty[x] Card Name
    match = re.match(r'^(\d+)x?\s+(.+)$', entry, re.IGNORECASE)
    if match:
        quantity = int(match.group(1))
        name = match.group(2).strip()
    else:
        quantity = 1
        name = entry
    
    return name, quantity, None, None


def parse_moxfield_csv(content: str) -> List[Tuple[str, int, Optional[str], Optional[str]]]:
    """Parse a Moxfield CSV export into card entries."""
    import csv
    from io import StringIO
    
    cards = []
    reader = csv.DictReader(StringIO(content))
    
    for row in reader:
        try:
            name = row.get('Name', '').strip()
            if not name:
                continue
            quantity = int(row.get('Count', row.get('Quantity', '1')))
            set_code = row.get('Edition', row.get('Set', '')).strip().lower() or None
            collector_num = str(row.get('Collector Number', row.get('Number', ''))).strip() or None
            cards.append((name, quantity, set_code, collector_num))
        except (ValueError, KeyError):
            continue
    
    return cards


def fetch_card_data(card_name: str) -> Optional[dict]:
    """Fetch card data from Scryfall API by fuzzy name."""
    url = f"{SCRYFALL_API}/cards/named"
    params = {"fuzzy": card_name}
    
    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        return None
    except requests.exceptions.RequestException:
        return None


def fetch_card_by_set(set_code: str, collector_number: str) -> Optional[dict]:
    """Fetch a specific card printing from Scryfall by set and collector number."""
    url = f"{SCRYFALL_API}/cards/{set_code}/{collector_number}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.HTTPError:
        return None
    except requests.exceptions.RequestException:
        return None


def get_image_url(card_data: dict, size: str = 'large') -> Optional[str]:
    """Extract image URL from card data."""
    if 'image_uris' in card_data:
        for key in [size, 'large', 'normal', 'small']:
            if key in card_data['image_uris']:
                return card_data['image_uris'][key]
    
    if 'card_faces' in card_data and len(card_data['card_faces']) > 0:
        face = card_data['card_faces'][0]
        if 'image_uris' in face:
            for key in [size, 'large', 'normal', 'small']:
                if key in face['image_uris']:
                    return face['image_uris'][key]
    
    return None


def download_image(url: str, card_name: str, use_cache: bool = True,
                    set_code: str = None, collector_number: str = None) -> Optional[Path]:
    """Download card image and return local path."""
    # Use set+collector for unique cache key when available (different art versions)
    if set_code and collector_number:
        filename = sanitize_filename(f"{card_name}_{set_code}_{collector_number}") + ".png"
    else:
        filename = sanitize_filename(card_name) + ".png"
    cache_path = CACHE_DIR / filename
    
    if use_cache and cache_path.exists():
        return cache_path
    
    try:
        response = requests.get(url, timeout=30)
        response.raise_for_status()
        
        with open(cache_path, 'wb') as f:
            f.write(response.content)
        
        return cache_path
    except requests.exceptions.RequestException:
        return None


def resize_card_image(image_path: Path, dpi: int = DEFAULT_DPI) -> Image.Image:
    """Resize card image to standard MTG dimensions at specified DPI."""
    target_width = int(CARD_WIDTH_INCHES * dpi)
    target_height = int(CARD_HEIGHT_INCHES * dpi)
    
    img = Image.open(image_path)
    
    if img.mode in ('RGBA', 'P'):
        background = Image.new('RGB', img.size, (255, 255, 255))
        if img.mode == 'P':
            img = img.convert('RGBA')
        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
        img = background
    elif img.mode != 'RGB':
        img = img.convert('RGB')
    
    img_ratio = img.width / img.height
    target_ratio = target_width / target_height
    
    if img_ratio > target_ratio:
        new_height = target_height
        new_width = int(new_height * img_ratio)
    else:
        new_width = target_width
        new_height = int(new_width / img_ratio)
    
    img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)
    
    left = (new_width - target_width) // 2
    top = (new_height - target_height) // 2
    img = img.crop((left, top, left + target_width, top + target_height))
    
    return img


def create_pdf(cards: List[Tuple[str, Image.Image]], output_path: str, dpi: int = DEFAULT_DPI):
    """Create PDF with cards arranged in a grid."""
    page_width, page_height = LETTER
    
    card_width_pts = CARD_WIDTH_INCHES * 72
    card_height_pts = CARD_HEIGHT_INCHES * 72
    
    grid_width = CARDS_PER_ROW * card_width_pts
    grid_height = CARDS_PER_COL * card_height_pts
    margin_x = (page_width - grid_width) / 2
    margin_y = (page_height - grid_height) / 2
    
    c = canvas.Canvas(output_path, pagesize=LETTER)
    
    card_index = 0
    total_cards = len(cards)
    
    while card_index < total_cards:
        for row in range(CARDS_PER_COL):
            for col in range(CARDS_PER_ROW):
                if card_index >= total_cards:
                    break
                
                card_name, card_img = cards[card_index]
                
                x = margin_x + col * card_width_pts
                y = page_height - margin_y - (row + 1) * card_height_pts
                
                temp_path = CACHE_DIR / f"temp_{card_index}.jpg"
                card_img.save(temp_path, "JPEG", quality=95)
                
                c.drawImage(str(temp_path), x, y, 
                           width=card_width_pts, height=card_height_pts)
                
                temp_path.unlink()
                
                card_index += 1
        
        if card_index < total_cards:
            c.showPage()
    
    c.save()


class MTGProxyGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("MTG Proxy Printer")
        self.root.geometry("1200x800")
        self.root.minsize(900, 600)
        
        # Card data storage
        self.card_images = []  # List of (name, PIL.Image) for PDF
        self.preview_images = []  # List of PhotoImage for display
        self.card_photo_refs = []  # Keep references to prevent garbage collection
        
        self.setup_ui()
        setup_cache()
    
    def setup_ui(self):
        """Setup the main UI components."""
        # Main container
        main_frame = ttk.Frame(self.root, padding="10")
        main_frame.pack(fill=tk.BOTH, expand=True)
        
        # Left panel - Decklist input
        left_frame = ttk.LabelFrame(main_frame, text="Decklist", padding="10")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=False, padx=(0, 5))
        
        # Decklist text area
        text_frame = ttk.Frame(left_frame)
        text_frame.pack(fill=tk.BOTH, expand=True)
        
        self.decklist_text = tk.Text(text_frame, width=35, height=20, wrap=tk.NONE)
        text_scrollbar_y = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, 
                                          command=self.decklist_text.yview)
        text_scrollbar_x = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, 
                                          command=self.decklist_text.xview)
        
        self.decklist_text.configure(yscrollcommand=text_scrollbar_y.set,
                                      xscrollcommand=text_scrollbar_x.set)
        
        self.decklist_text.grid(row=0, column=0, sticky="nsew")
        text_scrollbar_y.grid(row=0, column=1, sticky="ns")
        text_scrollbar_x.grid(row=1, column=0, sticky="ew")
        
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        
        # Example text
        example_text = """# Example Decklist
# Supports plain & Moxfield formats:
#   4x Lightning Bolt
#   1 Sol Ring (MH3) 532

4x Lightning Bolt
4x Counterspell
2x Sol Ring
1x Black Lotus
"""
        self.decklist_text.insert(tk.END, example_text)
        
        # Buttons frame
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=(10, 0))
        
        ttk.Button(btn_frame, text="Load File", 
                   command=self.load_decklist_file).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Load Moxfield CSV", 
                   command=self.load_moxfield_csv).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(btn_frame, text="Clear", 
                   command=self.clear_decklist).pack(side=tk.LEFT, padx=(0, 5))
        
        # Fetch button
        self.fetch_btn = ttk.Button(btn_frame, text="Fetch Cards", 
                                     command=self.fetch_cards)
        self.fetch_btn.pack(side=tk.RIGHT)
        
        # Right panel - Preview
        right_frame = ttk.LabelFrame(main_frame, text="Card Preview", padding="10")
        right_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(5, 0))
        
        # Preview canvas with scrollbar
        preview_container = ttk.Frame(right_frame)
        preview_container.pack(fill=tk.BOTH, expand=True)
        
        self.preview_canvas = tk.Canvas(preview_container, bg='#2b2b2b')
        preview_scrollbar_y = ttk.Scrollbar(preview_container, orient=tk.VERTICAL, 
                                             command=self.preview_canvas.yview)
        preview_scrollbar_x = ttk.Scrollbar(preview_container, orient=tk.HORIZONTAL, 
                                             command=self.preview_canvas.xview)
        
        self.preview_canvas.configure(yscrollcommand=preview_scrollbar_y.set,
                                       xscrollcommand=preview_scrollbar_x.set)
        
        preview_scrollbar_y.pack(side=tk.RIGHT, fill=tk.Y)
        preview_scrollbar_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.preview_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        
        # Frame inside canvas for cards
        self.cards_frame = ttk.Frame(self.preview_canvas)
        self.canvas_window = self.preview_canvas.create_window(
            (0, 0), window=self.cards_frame, anchor='nw'
        )
        
        self.cards_frame.bind('<Configure>', self.on_frame_configure)
        self.preview_canvas.bind('<Configure>', self.on_canvas_configure)
        
        # Enable mousewheel scrolling
        self.preview_canvas.bind_all("<MouseWheel>", self.on_mousewheel)
        
        # Bottom panel - Status and actions
        bottom_frame = ttk.Frame(right_frame)
        bottom_frame.pack(fill=tk.X, pady=(10, 0))
        
        # Progress bar
        self.progress_var = tk.DoubleVar()
        self.progress_bar = ttk.Progressbar(bottom_frame, variable=self.progress_var, 
                                             maximum=100)
        self.progress_bar.pack(fill=tk.X, pady=(0, 5))
        
        # Status label
        self.status_var = tk.StringVar(value="Ready - Enter your decklist and click 'Fetch Cards'")
        self.status_label = ttk.Label(bottom_frame, textvariable=self.status_var)
        self.status_label.pack(anchor=tk.W)
        
        # Export buttons
        export_frame = ttk.Frame(bottom_frame)
        export_frame.pack(fill=tk.X, pady=(10, 0))
        
        self.export_btn = ttk.Button(export_frame, text="Export PDF", 
                                      command=self.export_pdf, state=tk.DISABLED)
        self.export_btn.pack(side=tk.RIGHT)
        
        # Card count label
        self.count_var = tk.StringVar(value="")
        ttk.Label(export_frame, textvariable=self.count_var).pack(side=tk.LEFT)
    
    def on_frame_configure(self, event):
        """Reset the scroll region to encompass the inner frame."""
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
    
    def on_canvas_configure(self, event):
        """When canvas resizes, update the width of the inner frame."""
        canvas_width = event.width
        self.preview_canvas.itemconfig(self.canvas_window, width=canvas_width)
    
    def on_mousewheel(self, event):
        """Handle mousewheel scrolling."""
        self.preview_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
    
    def load_decklist_file(self):
        """Load a decklist from a file (plain text or Moxfield text export)."""
        filepath = filedialog.askopenfilename(
            title="Select Decklist File",
            filetypes=[
                ("Text files", "*.txt"),
                ("CSV files", "*.csv"),
                ("All files", "*.*")
            ]
        )
        
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Auto-detect CSV (Moxfield CSV export)
                if filepath.lower().endswith('.csv') or content.lstrip().startswith('Count,'):
                    self._load_csv_content(content, filepath)
                else:
                    self.decklist_text.delete(1.0, tk.END)
                    self.decklist_text.insert(tk.END, content)
                    self.status_var.set(f"Loaded: {os.path.basename(filepath)}")
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load file: {e}")
    
    def load_moxfield_csv(self):
        """Load a Moxfield CSV export file."""
        filepath = filedialog.askopenfilename(
            title="Select Moxfield CSV Export",
            filetypes=[
                ("CSV files", "*.csv"),
                ("All files", "*.*")
            ]
        )
        
        if filepath:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self._load_csv_content(content, filepath)
            except Exception as e:
                messagebox.showerror("Error", f"Failed to load CSV: {e}")
    
    def _load_csv_content(self, content: str, filepath: str):
        """Parse CSV content and populate the decklist text area."""
        cards = parse_moxfield_csv(content)
        if not cards:
            messagebox.showwarning("Empty", "No cards found in the CSV file.")
            return
        
        # Convert to Moxfield-style text so the user can see/edit
        lines = [f"# Imported from {os.path.basename(filepath)}\n"]
        for name, qty, set_code, col_num in cards:
            if set_code and col_num:
                lines.append(f"{qty} {name} ({set_code.upper()}) {col_num}")
            else:
                lines.append(f"{qty}x {name}")
        
        self.decklist_text.delete(1.0, tk.END)
        self.decklist_text.insert(tk.END, '\n'.join(lines))
        self.status_var.set(
            f"Loaded {len(cards)} unique cards from {os.path.basename(filepath)}"
        )
    
    def clear_decklist(self):
        """Clear the decklist text area."""
        self.decklist_text.delete(1.0, tk.END)
        self.clear_preview()
        self.status_var.set("Decklist cleared")
    
    def clear_preview(self):
        """Clear the preview area."""
        for widget in self.cards_frame.winfo_children():
            widget.destroy()
        self.card_images.clear()
        self.preview_images.clear()
        self.card_photo_refs.clear()
        self.export_btn.config(state=tk.DISABLED)
        self.count_var.set("")
        self.progress_var.set(0)
    
    def parse_decklist(self) -> List[Tuple[str, int, Optional[str], Optional[str]]]:
        """Parse the decklist text into card entries with optional set info."""
        content = self.decklist_text.get(1.0, tk.END)
        cards = []
        
        for line in content.split('\n'):
            name, qty, set_code, col_num = parse_card_entry(line)
            if name:
                cards.append((name, qty, set_code, col_num))
        
        return cards
    
    def fetch_cards(self):
        """Start fetching cards in a background thread."""
        card_entries = self.parse_decklist()
        
        if not card_entries:
            messagebox.showwarning("No Cards", "Please enter some cards in the decklist.")
            return
        
        self.clear_preview()
        self.fetch_btn.config(state=tk.DISABLED)
        self.status_var.set("Fetching cards...")
        
        # Start background thread
        thread = threading.Thread(target=self.fetch_cards_thread, args=(card_entries,))
        thread.daemon = True
        thread.start()
    
    def fetch_cards_thread(self, card_entries: List[Tuple[str, int, Optional[str], Optional[str]]]):
        """Background thread for fetching cards."""
        total_cards = sum(qty for _, qty, *_ in card_entries)
        fetched = 0
        errors = []
        
        for entry in card_entries:
            card_name, quantity = entry[0], entry[1]
            set_code = entry[2] if len(entry) > 2 else None
            collector_num = entry[3] if len(entry) > 3 else None
            
            # Update status
            display = card_name
            if set_code and collector_num:
                display = f"{card_name} ({set_code.upper()}) #{collector_num}"
            self.root.after(0, lambda n=display: 
                           self.status_var.set(f"Fetching: {n}..."))
            
            # Respect Scryfall rate limit
            time.sleep(0.1)
            
            # Try specific printing first, fall back to fuzzy name
            card_data = None
            if set_code and collector_num:
                card_data = fetch_card_by_set(set_code, collector_num)
            if not card_data:
                card_data = fetch_card_data(card_name)
            
            if not card_data:
                errors.append(card_name)
                fetched += quantity
                self.root.after(0, lambda f=fetched: 
                               self.progress_var.set((f / total_cards) * 100))
                continue
            
            actual_name = card_data.get('name', card_name)
            actual_set = card_data.get('set', set_code)
            actual_num = card_data.get('collector_number', collector_num)
            
            # Get large image for PDF
            image_url_large = get_image_url(card_data, 'large')
            if not image_url_large:
                errors.append(card_name)
                fetched += quantity
                self.root.after(0, lambda f=fetched: 
                               self.progress_var.set((f / total_cards) * 100))
                continue
            
            image_path = download_image(image_url_large, actual_name,
                                         set_code=actual_set,
                                         collector_number=actual_num)
            if not image_path:
                errors.append(card_name)
                fetched += quantity
                self.root.after(0, lambda f=fetched: 
                               self.progress_var.set((f / total_cards) * 100))
                continue
            
            # Create resized image for PDF
            pdf_image = resize_card_image(image_path)
            
            # Create preview thumbnail
            preview_img = Image.open(image_path)
            preview_img.thumbnail((PREVIEW_CARD_WIDTH, PREVIEW_CARD_HEIGHT), 
                                   Image.Resampling.LANCZOS)
            
            # Build display label
            label = actual_name
            if actual_set:
                label = f"{actual_name} [{actual_set.upper()}]"
            
            # Add cards for quantity
            for i in range(quantity):
                self.card_images.append((actual_name, pdf_image))
                
                # Add to preview (in main thread)
                self.root.after(0, lambda img=preview_img.copy(), name=label: 
                               self.add_preview_card(img, name))
                
                fetched += 1
                self.root.after(0, lambda f=fetched: 
                               self.progress_var.set((f / total_cards) * 100))
        
        # Finish up
        self.root.after(0, lambda: self.fetch_complete(total_cards, len(errors), errors))
    
    def add_preview_card(self, pil_image: Image.Image, card_name: str):
        """Add a card image to the preview grid."""
        # Convert to PhotoImage
        photo = ImageTk.PhotoImage(pil_image)
        self.card_photo_refs.append(photo)  # Keep reference
        
        # Calculate grid position
        num_cards = len(self.card_photo_refs) - 1
        cards_per_row = max(1, (self.preview_canvas.winfo_width() - 20) // (PREVIEW_CARD_WIDTH + 10))
        if cards_per_row < 1:
            cards_per_row = 5
        
        row = num_cards // cards_per_row
        col = num_cards % cards_per_row
        
        # Create frame for card
        card_frame = ttk.Frame(self.cards_frame)
        card_frame.grid(row=row, column=col, padx=5, pady=5, sticky='nw')
        
        # Card image label
        img_label = ttk.Label(card_frame, image=photo)
        img_label.pack()
        
        # Card name label (truncated)
        display_name = card_name[:18] + "..." if len(card_name) > 18 else card_name
        name_label = ttk.Label(card_frame, text=display_name, font=('TkDefaultFont', 8))
        name_label.pack()
        
        # Update scroll region
        self.cards_frame.update_idletasks()
        self.preview_canvas.configure(scrollregion=self.preview_canvas.bbox("all"))
    
    def fetch_complete(self, total: int, error_count: int, errors: List[str]):
        """Called when fetching is complete."""
        self.fetch_btn.config(state=tk.NORMAL)
        
        if self.card_images:
            self.export_btn.config(state=tk.NORMAL)
            pages = (len(self.card_images) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE
            self.count_var.set(f"{len(self.card_images)} cards ({pages} pages)")
        
        if error_count > 0:
            error_msg = f"Completed with {error_count} error(s): {', '.join(errors[:5])}"
            if error_count > 5:
                error_msg += f" and {error_count - 5} more"
            self.status_var.set(error_msg)
        else:
            self.status_var.set(f"Successfully fetched {total} cards!")
    
    def export_pdf(self):
        """Export cards to PDF."""
        if not self.card_images:
            messagebox.showwarning("No Cards", "No cards to export. Fetch cards first.")
            return
        
        filepath = filedialog.asksaveasfilename(
            title="Save PDF",
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")]
        )
        
        if not filepath:
            return
        
        try:
            self.status_var.set("Generating PDF...")
            self.root.update()
            
            create_pdf(self.card_images, filepath)
            
            pages = (len(self.card_images) + CARDS_PER_PAGE - 1) // CARDS_PER_PAGE
            self.status_var.set(f"PDF saved: {os.path.basename(filepath)}")
            
            messagebox.showinfo(
                "Success", 
                f"Created {os.path.basename(filepath)}\n"
                f"{len(self.card_images)} cards on {pages} page(s)\n\n"
                f"Print at 100% scale for correct card size."
            )
        except Exception as e:
            messagebox.showerror("Error", f"Failed to create PDF: {e}")
            self.status_var.set("PDF export failed")


def main():
    root = tk.Tk()
    
    # Set icon if available
    try:
        root.iconbitmap(default='')
    except:
        pass
    
    # Apply a theme
    style = ttk.Style()
    if 'clam' in style.theme_names():
        style.theme_use('clam')
    
    app = MTGProxyGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
