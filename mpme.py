import abc
import dataclasses
import eyed3
import os
import platform
import shutil
import subprocess
import sys
import time


#################
### CONSTANTS ###
#################
PLATFORM = platform.platform()
BAD_SONG_CHARS = ('\'', '"', '\n')
MISC_SONG_FOLDER_NAME = 'OTHER'
TMP_DOWNLOAD_DIR = '__downloads__'
AUDIO_FORMAT = 'mp3'
IGNORE_DISKS = {'Macintosh HD'}
MAC_VOLUMES_DIR = '/Volumes'
LINUX_MOUNT_DIR = '/mnt'
BACKUP_DIR = 'backup'
RETRY_ATTEMPTS = 3
MAC_PLATFORM_PART = 'macOS'
LINUX_PLATFORM_PART = 'Linux'

UNSUPPORTED_OS_MSG = 'Windows? Fuck off'

###############
### HELPERS ###
###############

def mprint(message):
  print(f'\n{message}\n')


######################
### HELPER CLASSES ###
######################

@dataclasses.dataclass
class Song:
  name: str
  artist: str

  @property
  def search_term(self):
    return f'{self.name} {self.artist}'

  @property
  def full_name(self):
    return f'{self.name} - {self.artist}'

  @property
  def file_name(self):
    return f'{self.full_name}.{AUDIO_FORMAT}'

  @classmethod
  def from_string(cls, string):
    if string := cls.clean_string(string):
      try:
        return cls(*string.split(','))
      except TypeError:
        pass

  @classmethod
  def from_file(cls, path):
    eyed3_file = eyed3.load(path)
    name = path.split('/')[-1].replace(
      f'.{AUDIO_FORMAT}', '')
    if not eyed3_file:
      return cls(name=name, artist='')

    # Use album as MP3 Players organise these better
    artist = eyed3_file.tag.album
    name = name.replace(f',{artist}' or '', '')
    return cls(name=name, artist=artist)

  @staticmethod
  def clean_string(string):
    for char in BAD_SONG_CHARS:
      string = string.replace(char, '')
    return string.strip()

  def __str__(self):
    return f'<{self.artist}: {self.name}>'

class SongList:

  def __init__(self):
    self.songs = []

  def populate(self):
    options = (
      ('from file', self.populate_from_file),
      ('from URL', self.populate_from_url),
      ('manual', self.populate_from_input))

    display_choices = ' '.join(
        f'\n({index + 1}) {name}'
        for index, (name,_) in enumerate(options))
    while True:
      choice = input(
        f'Please choose an option: {display_choices}\n:')
      try:
        self.songs = [
          Song.from_string(line)
          for line in options[int(choice) - 1][1]()
          if line
        ]
        break
      except (IndexError, ValueError):
        pass

  def populate_from_file(self):
    with open(input('Enter file name path: ')) as data:
      return data.read().split('\n')

  def populate_from_url(self):
    raise Exception('Not implemented!')

  def populate_from_input(self):
    mprint(
      'Paste a list of song + artist names'
      '(Use ENTER then ctrl D when done): ')
    return sys.stdin.readlines()

  def __str__(self):
    return '\n'.join(str(song) for song in self.songs)


############
### CORE ###
############

class Fetcher:

  def __init__(self, song_list):
    self.song_list = song_list

  def begin(self):
    self.prepare()
    self.fetch_all_songs()

  def prepare(self):
    mprint('Clearing downloads...')
    if os.path.exists(TMP_DOWNLOAD_DIR):
      shutil.rmtree(TMP_DOWNLOAD_DIR)
    os.mkdir(TMP_DOWNLOAD_DIR)
    mprint('Updating YouTube DL...')
    subprocess.check_output(['youtube-dl', '-U'])

  def fetch_all_songs(self):
    mprint(f'Fetching {len(self.song_list.songs)} songs...')
    for song in self.song_list.songs:
      if success := self.fetch_song(song):
        self.tag_song(song)
    mprint('Done!')

  def fetch_song(self, song):
    mprint(f'Fetching {song}...')
    path = os.path.join(TMP_DOWNLOAD_DIR, song.full_name)
    for attempt in range(RETRY_ATTEMPTS):
      try:
        subprocess.check_output([
          'youtube-dl',
          '-x',
          '-o', f'{path}.%(ext)s',
          f'ytsearch:{song.search_term}',
          '--audio-format', AUDIO_FORMAT
        ])
        break
      except Exception as error:
        mprint(f'Issue: {error}')
        mprint(f'Retrying {attempt + 1}/{RETRY_ATTEMPTS}')
    else:
      mprint('Unable to fetch song. Skipping...')
      return
    return True

  def tag_song(self, song):
    eyed3_file = eyed3.load(
      os.path.join(TMP_DOWNLOAD_DIR, song.file_name))

    # Use album as MP3 Players organise these better
    eyed3_file.tag.album = song.artist
    eyed3_file.tag.save()


class Exporter(abc.ABC):

  @abc.abstractmethod
  def export(self):
    ...


class ExternalDiskExporter(Exporter):

  name = 'MP3 Player (external disk)'

  def export(self):
    disk_name = self.find_disks()
    if LINUX_PLATFORM_PART in PLATFORM:
      subprocess.check_call(['mount', f'/dev/{disk_name}', LINUX_MOUNT_DIR])
      path = LINUX_MOUNT_DIR
    elif MAC_PLATFORM_PART in PLATFORM:
      path = os.path.join(MAC_VOLUMES_DIR, disk_name)
    else:
      raise Exception(UNSUPPORTED_OS_MSG)
    print('Exporting songs to disk...')
    shutil.copytree(TMP_DOWNLOAD_DIR, path, dirs_exist_ok=True)

  def find_disks(self):
    mprint('Searching for disks...')
    while True:
      if LINUX_PLATFORM_PART in PLATFORM:
        available_disks = {disk for disk in os.listdir('/dev') if 'sd' in disk}
      elif MAC_PLATFORM_PART in PLATFORM:
        available_disks = set(os.listdir(MAC_VOLUMES_DIR))
      else:
        raise Exception(UNSUPPORTED_OS_MSG)
      available_disks = list(available_disks - IGNORE_DISKS)
      if not available_disks:
        time.sleep(1)
        continue
      display_disks = '\n'.join(
        f'({index + 1}) {disk}'
        for index, disk in enumerate(available_disks))
      try:
        choice = input(f'Which disk?\n {display_disks}\n:')
        return available_disks[int(choice) - 1]
      except (IndexError, ValueError):
        pass


class LocalExporter(Exporter):

  name = 'Local (backup)'

  def export(self):
    if not os.path.exists(BACKUP_DIR):
      os.mkdir(BACKUP_DIR)
    shutil.copytree(
      TMP_DOWNLOAD_DIR, BACKUP_DIR, dirs_exist_ok=True)


class GoogleDriveExporter(Exporter):

  name = 'Google Drive'

  def export(self):
    raise Exception('Not implemented!')


EXPORTERS = (
  ExternalDiskExporter,
  LocalExporter,
  GoogleDriveExporter,
)


def offer_exports():
    for exporter in EXPORTERS:
      export = (input(
        f'Export to {exporter.name} [y]: ') or 'y') == 'y'
      if export:
        exporter().export()
        mprint('Done!')

############
### MAIN ###
############

def show_introduction():
  print(
    """
** Welcome to MPME! **

Scan, download and export music to an external source.

Ready? Hit Enter!
    """)
  input()


def main():
  show_introduction()

  song_list = SongList()
  song_list.populate()

  fetcher = Fetcher(song_list)
  fetcher.begin()

  offer_exports()


  mprint('Finished. Quitting.')

if __name__ == '__main__':
  main()
