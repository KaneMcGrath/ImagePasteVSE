bl_info = {
    "name": "PasteImageVSE",
    "author": "PasteImageVSE",
    "version": (1, 0, 0),
    "blender": (4, 0, 0),
    "location": "Video Sequence Editor > Add > Image from Clipboard",
    "description": "Paste an image from the clipboard into the Video Sequence Editor",
    "category": "Sequencer",
}

import bpy
import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path
from time import strftime

# ---------------------------------------------------------------------------
# Clipboard helpers (cross-platform, adapted from BagaPaste)
# ---------------------------------------------------------------------------

def save_clipboard_image_to(path_no_ext: Path) -> Path | None:
    """Save the image currently in the system clipboard to a PNG file.

    Returns the path to the saved file, or *None* if no image was found.
    """
    out_path = path_no_ext.with_suffix(".png")
    plat = sys.platform

    # -- Windows ------------------------------------------------------------
    if plat.startswith("win"):
        ps = rf"""
        Add-Type -AssemblyName System.Windows.Forms | Out-Null
        Add-Type -AssemblyName System.Drawing | Out-Null
        try {{
            $img = Get-Clipboard -Format Image -ErrorAction SilentlyContinue
        }} catch {{ $img = $null }}
        if ($img -ne $null) {{
            $dst = "{out_path}".Replace('\','\\')
            $img.Save($dst, [System.Drawing.Imaging.ImageFormat]::Png)
            Write-Output $dst
        }}
        """
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
        )
        res = completed.stdout.strip()
        if res and os.path.isfile(res):
            return Path(res)

    # -- macOS --------------------------------------------------------------
    elif plat == "darwin":
        script = f'''
        try
            set theFile to (POSIX file "{out_path}") as «class furl»
            set theData to the clipboard as «class PNGf»
            set fileRef to open for access theFile with write permission
            set eof of fileRef to 0
            write theData to fileRef
            close access fileRef
            return "{out_path}"
        on error
            return ""
        end try
        '''
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
        )
        res = completed.stdout.strip()
        if res and os.path.isfile(res):
            return Path(res)

    # -- Linux (Wayland / X11) ----------------------------------------------
    elif plat.startswith("linux"):
        if shutil.which("wl-paste"):  # Wayland
            with open(out_path, "wb") as f:
                subprocess.run(
                    ["wl-paste", "-t", "image/png"],
                    stdout=f, stderr=subprocess.DEVNULL,
                )
            if out_path.is_file() and out_path.stat().st_size > 0:
                return out_path
        elif shutil.which("xclip"):  # X11
            with open(out_path, "wb") as f:
                subprocess.run(
                    ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                    stdout=f, stderr=subprocess.DEVNULL,
                )
            if out_path.is_file() and out_path.stat().st_size > 0:
                return out_path

    return None


def get_clipboard_filedroplist_image() -> Path | None:
    """Return the first image file path found in a file-drop clipboard entry."""
    plat = sys.platform
    exts = ('.png', '.jpg', '.jpeg', '.webp', '.tif', '.tiff',
            '.bmp', '.gif', '.exr', '.hdr', '.dds', '.heic')
    files: list[str] = []

    # -- Windows ------------------------------------------------------------
    if plat.startswith("win"):
        ps = r"""
        try {
            $files = Get-Clipboard -Format FileDropList -ErrorAction SilentlyContinue
        } catch { $files = $null }
        if ($files -ne $null -and $files.Count -gt 0) {
            foreach ($f in $files) { Write-Output $f }
        }
        """
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
        )
        files = completed.stdout.strip().splitlines()

    # -- macOS --------------------------------------------------------------
    elif plat == "darwin":
        script = '''
        try
            set theData to the clipboard as list of «class furl»
            set out to ""
            repeat with i in theData
                set out to out & (POSIX path of i) & "\n"
            end repeat
            return out
        on error
            return ""
        end try
        '''
        completed = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True,
        )
        files = completed.stdout.strip().splitlines()

    # -- Linux (X11) --------------------------------------------------------
    elif plat.startswith("linux"):
        if shutil.which("xclip"):
            completed = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True, text=True,
            )
            lines = completed.stdout.strip().splitlines()
            import urllib.parse
            for line in lines:
                if line.startswith("file://"):
                    try:
                        files.append(urllib.parse.unquote(line[7:]))
                    except Exception:
                        pass
                elif line.startswith("/"):
                    files.append(line)

    for f in files:
        f = f.strip().strip('"').strip("'")
        if os.path.isfile(f) and f.lower().endswith(exts):
            return Path(f)

    return None


def get_clipboard_text() -> str | None:
    txt = bpy.context.window_manager.clipboard
    return txt if txt else None


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

def get_addon_prefs():
    return bpy.context.preferences.addons[__package__].preferences


def storage_dir() -> Path:
    """Return the directory where clipboard images are saved before inserting."""
    prefs = get_addon_prefs()
    if prefs.storage_mode == "CUSTOM" and prefs.storage_dir:
        try:
            custom = Path(bpy.path.abspath(prefs.storage_dir))
            custom.mkdir(parents=True, exist_ok=True)
            return custom
        except Exception:
            pass
    if prefs.storage_mode == "TEMP":
        return Path(tempfile.gettempdir())
    fp = bpy.data.filepath
    if fp:
        return Path(fp).parent
    return Path(tempfile.gettempdir())


# ---------------------------------------------------------------------------
# Operator – Paste image from clipboard into VSE
# ---------------------------------------------------------------------------

class SEQUENCER_OT_paste_image_from_clipboard(bpy.types.Operator):
    """Paste an image from the system clipboard as a new strip in the VSE"""

    bl_idname = "sequencer.paste_image_from_clipboard"
    bl_label = "Image from Clipboard"
    bl_description = "Paste image from clipboard as a new strip"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == "SEQUENCE_EDITOR"

    def execute(self, context):
        prefs = get_addon_prefs()
        target_dir = storage_dir()
        timestamp = strftime("%Y%m%d_%H%M%S")
        base = target_dir / f"clipboard_vse_{timestamp}"

        # 1) Try a raw bitmap image from the clipboard
        img_path = save_clipboard_image_to(base)

        # 2) Fallback: file-drag (e.g. Ctrl+C on a file in Explorer)
        if img_path is None:
            img_path = get_clipboard_filedroplist_image()

        # 3) Fallback: text (path or URL) – not implemented for VSE simplicity
        # but we keep the slot if users want to extend later.

        if img_path is None or not img_path.exists():
            self.report({"ERROR"}, "No image found in the clipboard. "
                        "Copy an image (e.g. Print Screen, Win+Shift+S, or Ctrl+C on a file).")
            return {"CANCELLED"}

        # ── Add the image as a VSE strip ───────────────────────────────
        if context.scene.sequence_editor is None:
            context.scene.sequence_editor_create()

        directory = str(img_path.parent)
        filename = img_path.name

        # Default channel
        channel = 1
        frame_start = context.scene.frame_current

        try:
            bpy.ops.sequencer.image_strip_add(
                directory=directory,
                files=[{"name": filename}],
                frame_start=frame_start,
                channel=channel,
                replace_sel=False,
                fit_method=prefs.fit_method,
            )
        except RuntimeError as exc:
            self.report({"ERROR"}, f"Failed to add image strip: {exc}")
            return {"CANCELLED"}

        self.report({"INFO"}, f"Added VSE strip: {img_path.name}")
        return {"FINISHED"}


# ---------------------------------------------------------------------------
# Menu – Insert into VSE Add menu
# ---------------------------------------------------------------------------

def vse_add_menu_draw(self, context):
    """Draw the 'Image from Clipboard' item in the VSE Add menu."""
    self.layout.separator()
    self.layout.operator(
        SEQUENCER_OT_paste_image_from_clipboard.bl_idname,
        icon="IMAGE_DATA",
        text="Image from Clipboard",
    )


# ---------------------------------------------------------------------------
# Panel – Optional side-panel for quick access
# ---------------------------------------------------------------------------

class SEQUENCER_PT_paste_image_clipboard(bpy.types.Panel):
    bl_label = "Paste Image"
    bl_idname = "SEQUENCER_PT_paste_image_clipboard"
    bl_space_type = "SEQUENCE_EDITOR"
    bl_region_type = "UI"
    bl_category = "Strip"

    @classmethod
    def poll(cls, context):
        return context.area and context.area.type == "SEQUENCE_EDITOR"

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.scale_y = 1.5
        row.operator(
            SEQUENCER_OT_paste_image_from_clipboard.bl_idname,
            icon="IMAGE_DATA",
            text="Paste Image from Clipboard",
        )


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------

class PasteImageVSEPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    storage_mode: bpy.props.EnumProperty(
        name="Storage",
        description="Where to save images imported from the clipboard",
        items=[
            ("BLEND_DIR", "Blend file folder",
             "Save next to the current .blend file"),
            ("TEMP", "Temporary folder",
             "Use the system temporary folder (discarded on reboot)"),
            ("CUSTOM", "Custom folder",
             "Use a folder you choose below"),
        ],
        default="TEMP",
    )

    storage_dir: bpy.props.StringProperty(
        name="Custom folder",
        description="Folder where clipboard images are saved",
        subtype="DIR_PATH",
        default="",
    )

    fit_method: bpy.props.EnumProperty(
        name="Fit method",
        description="How the image is fitted inside the strip",
        items=[
            ("FIT", "Fit", "Scale to fit within the strip bounds"),
            ("FILL", "Fill", "Scale to fill the strip bounds (may crop)"),
            ("STRETCH", "Stretch", "Stretch to strip bounds (ignores aspect)"),
            ("ORIGINAL", "Original", "Keep original size"),
        ],
        default="FIT",
    )

    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        col.label(text="Clipboard image storage:")
        col.prop(self, "storage_mode")
        if self.storage_mode == "CUSTOM":
            col.prop(self, "storage_dir")
        layout.separator()
        col = layout.column(align=True)
        col.label(text="Strip defaults:")
        col.prop(self, "fit_method")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

classes = (
    PasteImageVSEPreferences,
    SEQUENCER_OT_paste_image_from_clipboard,
    SEQUENCER_PT_paste_image_clipboard,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)

    # Add to the VSE Add menu
    # Blender 4.x+: SEQUENCER_MT_add is the main "Add" menu in the VSE
    try:
        bpy.types.SEQUENCER_MT_add.remove(vse_add_menu_draw)
    except Exception:
        pass
    bpy.types.SEQUENCER_MT_add.append(vse_add_menu_draw)


def unregister():
    try:
        bpy.types.SEQUENCER_MT_add.remove(vse_add_menu_draw)
    except Exception:
        pass
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
