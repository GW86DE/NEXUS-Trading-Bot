from pathlib import Path
import tkinter as tk
from tkinter import filedialog,messagebox
from settings_migration import migrate_from

def main():
    root=tk.Tk(); root.withdraw()
    folder=filedialog.askdirectory(title='Ordner der bisherigen TradingBot-Version auswählen')
    if not folder: return
    copied,skipped=migrate_from(Path(folder))
    msg='Übernommen:\n'+('\n'.join(copied) if copied else 'Keine neuen Dateien gefunden.')
    if skipped: msg+='\n\nBereits vorhanden und NICHT überschrieben:\n'+'\n'.join(skipped)
    messagebox.showinfo('Einstellungen übernehmen',msg)
if __name__=='__main__': main()
