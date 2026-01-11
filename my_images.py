# -*- coding: utf-8 -*-
#
# Media Search Dialog for Anki
# Adds a searchable media browser to the card editor context menu

import os
import re
from typing import List, Tuple, Optional
from pathlib import Path

from PyQt6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLineEdit,
    QScrollArea,
    QWidget,
    QLabel,
    QFrame,
    QInputDialog,
    QMenu,
)
from PyQt6.QtCore import Qt, QSize, pyqtSignal, QTimer
from PyQt6.QtGui import QPixmap, QCursor, QAction
from anki.hooks import addHook
from aqt import mw
from aqt.utils import tooltip, showWarning


# Check if the rename addon is already installed
HAS_RENAME_ADDON = False
try:
    # Check for the external editor/rename addon by looking for its hook
    import sys
    for module_name in sys.modules:
        if 'edit_insert_rename' in module_name.lower() or 'openinexternaleditor' in module_name.lower():
            HAS_RENAME_ADDON = True
            break
except:
    pass


def get_anki_point_version():
    """Get Anki point version for compatibility"""
    from anki import version as anki_version
    # Extract point version from version string like "2.1.54" -> 54
    match = re.search(r'(\d+)\.(\d+)\.(\d+)', anki_version)
    if match:
        return int(match.group(3))
    return 0


anki_point_version = get_anki_point_version()


def process_path(fname):
    """Process file path and return components"""
    media_folder = mw.col.media.dir()
    file_abs_path = os.path.join(media_folder, fname)
    base, ext = os.path.splitext(fname)
    return media_folder, file_abs_path, base, ext


def get_unused_new_name(media_folder, base, ext):
    """Get new filename from user via dialog"""
    new_name, ok = QInputDialog.getText(
        None,
        "Rename Image",
        "New filename (without extension):",
        text=base
    )
    
    if not ok or not new_name:
        return None
    
    # Clean the filename - replace spaces with underscores
    new_name = new_name.strip()
    new_name = new_name.replace(' ', '_')
    
    if not new_name:
        return None
    
    new_filename = new_name + ext
    
    # Check if file already exists
    new_path = os.path.join(media_folder, new_filename)
    if os.path.exists(new_path):
        showWarning(f"File '{new_filename}' already exists!")
        return None
    
    return new_filename


def _replace_all_img_src(orig_name: str, new_name: str):
    """Replace all occurrences of image in notes"""
    orig_name_escaped = re.escape(orig_name)
    
    # Find notes containing images
    if anki_point_version <= 49:
        n = mw.col.findNotes("<img")
    else:
        n = mw.col.find_notes("<img")
    
    # Simple literal string replacement patterns
    patterns_to_try = [
        (f'src="{orig_name}"', f'src="{new_name}"'),
        (f"src='{orig_name}'", f"src='{new_name}'"),
    ]
    
    # Also try unquoted if no spaces
    if " " not in orig_name:
        patterns_to_try.append((f'src={orig_name}', f'src="{new_name}"'))
    
    replaced_cnt = 0
    for old_pattern, new_pattern in patterns_to_try:
        if anki_point_version >= 28:
            op_chg_cnt = mw.col.backend.find_and_replace(
                nids=n,
                search=old_pattern,
                replacement=new_pattern,
                regex=False,  # Use literal string matching
                match_case=True,
                field_name=None,
            )
            if anki_point_version >= 45:
                replaced_cnt += op_chg_cnt.count
            else:
                replaced_cnt = op_chg_cnt
        else:
            from anki.find import findReplace
            replaced_cnt += findReplace(col=mw.col, nids=n, src=old_pattern, dst=new_pattern, regex=False, fold=False)
    
    return replaced_cnt


def replace_img_in_editor_and_reload(editor, orig_name, new_name):
    """Replace image reference in current editor field"""
    if not editor or not editor.note:
        return
    
    field = editor.currentField
    if field is None:
        return
    
    # Get current field content
    content = editor.note.fields[field]
    
    # Replace image references
    orig_escaped = re.escape(orig_name)
    patterns = [
        (f'src="{orig_escaped}"', f'src="{new_name}"'),
        (f"src='{orig_escaped}'", f"src='{new_name}'"),
    ]
    
    for old_pattern, new_pattern in patterns:
        content = content.replace(old_pattern, new_pattern)
    
    # Update field
    editor.note.fields[field] = content
    editor.loadNoteKeepingFocus()


def rename_image(editor, fname):
    """Rename an image file and update all references"""
    media_folder, file_abs_path, base, ext = process_path(fname)
    
    if not os.path.isfile(file_abs_path):
        showWarning(f"File not found: {fname}")
        return
    
    new_filename = get_unused_new_name(media_folder, base, ext)
    if not new_filename:
        return
    
    # Replace in all notes
    cnt = _replace_all_img_src(fname, new_filename)
    
    # Reset if needed
    if anki_point_version <= 44:
        mw.requireReset()
    
    # Rename the actual file
    old_abs_fname = os.path.join(media_folder, fname)
    new_abs_fname = os.path.join(media_folder, new_filename)
    
    if not os.path.isfile(new_abs_fname):
        os.rename(old_abs_fname, new_abs_fname)
    
    # Update current editor if applicable
    if editor:
        replace_img_in_editor_and_reload(editor, fname, new_filename)
    
    # Notify user
    s = f'Renamed file and updated {cnt} note{"s" if cnt != 1 else ""}: <br>from {fname} to {new_filename}'
    tooltip(s, period=4000)


class ImageThumbnail(QFrame):
    """Widget representing a single image thumbnail with filename"""
    
    clicked = pyqtSignal(str)
    
    def __init__(self, filepath: str, filename: str, highlight_text: str = "", editor=None):
        super().__init__()
        self.filepath = filepath
        self.filename = filename
        self.highlight_text = highlight_text
        self.preview_window = None
        self.editor = editor
        
        # Setup frame style
        self.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Plain)
        self.setLineWidth(1)
        self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
        self.setFixedWidth(160)  # Fixed width to prevent expansion
        
        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(5, 5, 5, 5)
        
        # Thumbnail image
        self.thumbnail_label = QLabel()
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setFixedSize(150, 150)
        self.thumbnail_label.setScaledContents(False)
        
        # Load and set thumbnail
        pixmap = QPixmap(filepath)
        if not pixmap.isNull():
            scaled_pixmap = pixmap.scaled(
                150, 150,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.thumbnail_label.setPixmap(scaled_pixmap)
        
        # Filename label with highlighting
        self.filename_label = QLabel()
        self.filename_label.setWordWrap(True)
        self.filename_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.filename_label.setStyleSheet("color: #999; font-size: 10px;")
        self.filename_label.setMaximumWidth(150)
        self.filename_label.setMinimumHeight(30)  # Give space for wrapping
        self.filename_label.setSizePolicy(
            self.filename_label.sizePolicy().horizontalPolicy(),
            self.filename_label.sizePolicy().Expanding
        )
        self.update_filename_display()
        
        layout.addWidget(self.thumbnail_label)
        layout.addWidget(self.filename_label)
        self.setLayout(layout)
    
    def update_filename_display(self):
        """Update filename label with highlighting"""
        if not self.highlight_text:
            self.filename_label.setText(self.filename)
            return
        
        # Create highlighted version
        highlighted = self.highlight_matches(self.filename, self.highlight_text)
        self.filename_label.setText(highlighted)
    
    def highlight_matches(self, text: str, search: str) -> str:
        """Add HTML highlighting to matching parts of text"""
        if not search:
            return text
        
        # Normalize for searching
        normalized_search = self.normalize_text(search)
        normalized_text = self.normalize_text(text)
        
        # Find all matching positions
        result = []
        last_pos = 0
        
        search_pos = 0
        for i, char in enumerate(text):
            norm_char = self.normalize_text(char)
            if search_pos < len(normalized_search) and norm_char == normalized_search[search_pos]:
                if search_pos == 0:
                    # Add text before match
                    result.append(text[last_pos:i])
                    last_pos = i
                search_pos += 1
                
                if search_pos == len(normalized_search):
                    # Complete match found
                    result.append(f'<span style="background-color: #ffff00; color: #000;">{text[last_pos:i+1]}</span>')
                    last_pos = i + 1
                    search_pos = 0
        
        # Add remaining text
        result.append(text[last_pos:])
        return ''.join(result)
    
    @staticmethod
    def normalize_text(text: str) -> str:
        """Normalize text for searching - lowercase, remove symbols"""
        # Replace underscores and hyphens with spaces
        text = text.replace('_', ' ').replace('-', ' ')
        # Remove most punctuation but keep alphanumeric
        text = re.sub(r'[^\w\s]', '', text)
        # Lowercase and collapse spaces
        return ' '.join(text.lower().split())
    
    def contextMenuEvent(self, event):
        """Show context menu on right-click"""
        menu = QMenu(self)
        
        # Add rename action (only if rename addon not installed)
        if not HAS_RENAME_ADDON:
            rename_action = menu.addAction("Rename Image...")
            rename_action.triggered.connect(lambda: rename_image(self.editor, self.filename))
        
        menu.exec(event.globalPos())
    
    def enterEvent(self, event):
        """Show large preview on hover"""
        self.show_preview()
        super().enterEvent(event)
    
    def leaveEvent(self, event):
        """Hide preview when mouse leaves"""
        self.hide_preview()
        super().leaveEvent(event)
    
    def show_preview(self):
        """Display large preview window"""
        if self.preview_window:
            return
        
        self.preview_window = QLabel(None, Qt.WindowType.ToolTip | Qt.WindowType.FramelessWindowHint)
        self.preview_window.setStyleSheet("border: 2px solid #666; background: white;")
        
        pixmap = QPixmap(self.filepath)
        if not pixmap.isNull():
            # Scale to max 600x600 while maintaining aspect ratio
            scaled = pixmap.scaled(
                600, 600,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            self.preview_window.setPixmap(scaled)
        
        # Position near cursor
        cursor_pos = QCursor.pos()
        self.preview_window.move(cursor_pos.x() + 20, cursor_pos.y() + 20)
        self.preview_window.show()
    
    def hide_preview(self):
        """Hide preview window"""
        if self.preview_window:
            self.preview_window.close()
            self.preview_window = None
    
    def mousePressEvent(self, event):
        """Handle click to insert image"""
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.filename)
        super().mousePressEvent(event)


class MediaSearchDialog(QDialog):
    """Dialog for searching and selecting media files"""
    
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.media_folder = mw.col.media.dir()
        self.all_images = self.get_all_images()
        self.current_matches = []
        self.loaded_count = 0
        self.load_batch_size = 10  # Load 10 at a time
        
        self.setWindowTitle("Search and Add Media")
        # Width based on thumbnail container: 160 (container) + scrollbar (~30) + padding = 210
        self.resize(210, 400)
        self.setMinimumWidth(210)
        
        # Main layout
        layout = QVBoxLayout()
        
        # Search box
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Type to search images...")
        self.search_box.textChanged.connect(self.on_search_changed)
        layout.addWidget(self.search_box)
        
        # Scroll area for results
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self.on_scroll)
        
        # Container for thumbnails
        self.results_widget = QWidget()
        self.results_layout = QVBoxLayout()
        self.results_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.results_widget.setLayout(self.results_layout)
        
        self.scroll_area.setWidget(self.results_widget)
        layout.addWidget(self.scroll_area)
        
        self.setLayout(layout)
        
        # Timer for debounced loading
        self.load_timer = QTimer()
        self.load_timer.setSingleShot(True)
        self.load_timer.timeout.connect(self.load_more_thumbnails)
        
        # Focus search box
        self.search_box.setFocus()
    
    def get_all_images(self) -> List[str]:
        """Get list of all image files in media folder"""
        image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg'}
        images = []
        
        try:
            for filename in os.listdir(self.media_folder):
                ext = os.path.splitext(filename)[1].lower()
                if ext in image_extensions:
                    images.append(filename)
        except Exception as e:
            print(f"Error reading media folder: {e}")
        
        return sorted(images)
    
    def on_scroll(self, value):
        """Load more thumbnails when scrolling near bottom"""
        scrollbar = self.scroll_area.verticalScrollBar()
        if scrollbar.maximum() > 0:
            # Load more when within 200px of bottom
            if value > scrollbar.maximum() - 200:
                if self.loaded_count < len(self.current_matches):
                    self.load_more_thumbnails()
    
    def on_search_changed(self, text: str):
        """Update results when search text changes"""
        # Clear existing results
        while self.results_layout.count():
            item = self.results_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.loaded_count = 0
        
        if not text.strip():
            self.current_matches = []
            return
        
        # Find matching images
        self.current_matches = self.find_matches(text)
        
        # Show message if no results
        if not self.current_matches:
            no_results = QLabel("No matching images found")
            no_results.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_results.setStyleSheet("color: #999; padding: 20px;")
            self.results_layout.addWidget(no_results)
            return
        
        # Load first batch immediately
        self.load_more_thumbnails()
    
    def load_more_thumbnails(self):
        """Load the next batch of thumbnails"""
        if self.loaded_count >= len(self.current_matches):
            return
        
        search_text = self.search_box.text()
        end_index = min(self.loaded_count + self.load_batch_size, len(self.current_matches))
        
        for i in range(self.loaded_count, end_index):
            filename, score = self.current_matches[i]
            filepath = os.path.join(self.media_folder, filename)
            thumbnail = ImageThumbnail(filepath, filename, search_text, self.editor)
            thumbnail.clicked.connect(self.on_image_selected)
            self.results_layout.addWidget(thumbnail)
        
        self.loaded_count = end_index
    
    def find_matches(self, search_text: str) -> List[Tuple[str, int]]:
        """Find and rank matching images"""
        normalized_search = ImageThumbnail.normalize_text(search_text)
        search_words = normalized_search.split()
        
        if not search_words:
            return []
        
        matches = []
        for filename in self.all_images:
            normalized_filename = ImageThumbnail.normalize_text(filename)
            
            # Calculate match score
            score = 0
            
            # Exact substring match (highest priority)
            if normalized_search in normalized_filename:
                score += 1000
            
            # All words present
            words_found = sum(1 for word in search_words if word in normalized_filename)
            if words_found == len(search_words):
                score += 100 * words_found
            else:
                score += 10 * words_found
            
            # Bonus for matching at start
            if normalized_filename.startswith(normalized_search):
                score += 500
            
            if score > 0:
                matches.append((filename, score))
        
        # Sort by score (descending)
        matches.sort(key=lambda x: x[1], reverse=True)
        return matches
    
    def on_image_selected(self, filename: str):
        """Insert selected image into editor"""
        if self.editor and self.editor.note:
            # Insert image at current cursor position using Anki's doPaste
            self.editor.doPaste(html=f'<img src="{filename}">', internal=True)
        
        self.accept()


def add_to_editor_context(view, menu):
    """Add media search option to editor right-click menu"""
    e = view.editor
    if not e or not e.note:
        return
    
    menu.addSeparator()
    
    action = menu.addAction("Search and Add Media...")
    action.triggered.connect(lambda: open_media_search(e))
    
    # Add rename option for images if rename addon not installed
    if not HAS_RENAME_ADDON:
        try:
            # Try to get the media URL from context
            url = None
            
            try:
                # Try Qt6 method first
                context_request = view.lastContextMenuRequest()
                if hasattr(context_request, 'mediaUrl'):
                    url = context_request.mediaUrl()
            except:
                try:
                    # Try Qt5 method as fallback
                    from aqt.qt import qtmajor
                    if qtmajor == 5:
                        from PyQt5.QtWebEngineWidgets import QWebEngineContextMenuData
                        context_data = view.page().contextMenuData()
                        url = context_data.mediaUrl()
                except:
                    pass
            
            if url and hasattr(url, 'isValid') and url.isValid():
                fname = url.fileName()
                fileabspath = os.path.join(mw.col.media.dir(), fname)
                
                if os.path.isfile(fileabspath):
                    rename_action = menu.addAction("Rename Image...")
                    rename_action.triggered.connect(lambda: rename_image(e, fname))
        except Exception as ex:
            # Silently fail if we can't add rename option
            pass


def open_media_search(editor):
    """Open the media search dialog"""
    dialog = MediaSearchDialog(editor, parent=editor.widget)
    dialog.exec()


# Register the hook
addHook("EditorWebView.contextMenuEvent", add_to_editor_context)