"""Non-mail UI preferences only; no identity, content, credentials or tokens."""
import json
import os
from pathlib import Path
import sqlite3
from contextlib import closing

def app_data_directory():
    base = os.environ.get('LOCALAPPDATA')
    if not base: raise RuntimeError('Windows user data directory unavailable')
    return Path(base) / 'CoremailDesktop'

class Preferences:
    ALLOWED = frozenset({'window_width','window_height','window_maximized','splitter_sizes','theme','font_size'})
    def __init__(self,directory):
        directory = Path(directory)
        directory.mkdir(parents=True,exist_ok=True)
        self.path = directory / 'preferences.sqlite3'
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS preferences (key TEXT PRIMARY KEY, value TEXT NOT NULL)')
    def set(self,key,value):
        if key not in self.ALLOWED: raise ValueError('Unsupported preference')
        if key == 'theme':
            if value not in ('system','light','dark'): raise ValueError('Invalid theme')
        elif key == 'splitter_sizes':
            if not isinstance(value,list) or len(value) != 3 or any(type(x) is not int or not 0 <= x <= 20000 for x in value): raise ValueError('Invalid sizes')
        elif key == 'window_maximized':
            if type(value) is not bool: raise ValueError('Invalid flag')
        elif type(value) is not int or not 1 <= value <= 20000: raise ValueError('Invalid size')
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)',(key,json.dumps(value)))
    def get(self,key,default=None):
        if key not in self.ALLOWED: raise ValueError('Unsupported preference')
        with closing(sqlite3.connect(self.path)) as db, db:
            row = db.execute('SELECT value FROM preferences WHERE key=?',(key,)).fetchone()
        return json.loads(row[0]) if row else default

