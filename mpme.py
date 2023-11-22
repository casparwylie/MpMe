import abc
import dataclasses
import os
import platform
import shutil
import json
from datetime import timedelta
import subprocess
import sys
import time

import yt_dlp
import eyed3


#################
### CONSTANTS ###
#################
PLATFORM = platform.platform()
MISC_SONG_FOLDER_NAME = 'OTHER'
TMP_DOWNLOAD_DIR = 'downloads'
AUDIO_FORMAT = 'mp3'
IGNORE_DISKS = {'Macintosh HD'}
MAC_VOLUMES_DIR = '/Volumes'
LINUX_MOUNT_DIR = '/mnt'
BACKUP_DIR = 'backup'
RETRY_ATTEMPTS = 3
MAC_PLATFORM_PART = 'macOS'
LINUX_PLATFORM_PART = 'Linux'
SONG_DELIM_CHAR = '~'
RESET_DOWNLOADS_EACH_RUN = False

UNSUPPORTED_OS_MSG = 'Windows? Fuck off'

###############
### HELPERS ###
###############

class YTDLogger:
    def debug(self, msg):
        if msg.startswith('[debug] '):
            pass
        else:
            self.info(msg)

    def info(self, msg):
        pass

    def warning(self, msg):
        print(msg)

    def error(self, msg):
        print(msg)

def mprint(message):
  print(f'\n{message}\n')


YDL_BASE_OPTS = {
    'format': 'bestaudio/best',
    'logger': YTDLogger(),
    'postprocessors': [{
        'key': 'FFmpegExtractAudio',
        'preferredcodec': 'mp3',
        'preferredquality': '192',
    }],
    # May fix 403s according to https://stackoverflow.com/questions/32104702/youtube-dl-library-and-error-403-forbidden-when-using-generated-direct-link-by
    # 'cachedir': False,
}


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
    return f'{self.name} {SONG_DELIM_CHAR} {self.artist}'

  @property
  def file_name(self):
    return f'{self.full_name}.{AUDIO_FORMAT}'

  @classmethod
  def from_string(cls, string):
    return cls(*string.split(SONG_DELIM_CHAR))

  def __str__(self):
    return f'<{self.artist}: {self.name}>'

class SongList:

  def __init__(self):
    self.songs = []

  def populate(self):
    options = (
      ('from file', self.populate_from_file),
      ('from json', self.populate_from_json),
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
          for line in sorted(options[int(choice) - 1][1]())
          if line
        ]
        break
      except (IndexError, ValueError):
        pass

  def populate_from_file(self):
    """Excepts a linear list of Title~Artist"""
    with open(input('Enter file name path: ')) as data:
      return data.read().split('\n')

  def populate_from_json(self):
    """Expects a JSON object of "Artist": ["Title 1", ...]"""
    rows = []
    with open(input('Enter file name path: ')) as data:
      data = json.load(data)
      for artist, titles in data.items():
        for title in titles:
          rows.append(f"{title}{SONG_DELIM_CHAR}{artist}")
      return rows

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
    self.failed = []

  def begin(self):
    self.prepare()
    self.fetch_all_songs()

  def prepare(self):
    if RESET_DOWNLOADS_EACH_RUN:
      mprint('Clearing downloads...')
      if os.path.exists(TMP_DOWNLOAD_DIR):
        shutil.rmtree(TMP_DOWNLOAD_DIR)
      os.mkdir(TMP_DOWNLOAD_DIR)

  def fetch_all_songs(self):
    start = int(input("Start at index [0]: ") or 0)
    total = len(self.song_list.songs)
    average_download_seconds = 0
    total_download_seconds = 0
    eta_seconds = 0
    for i, song in enumerate(self.song_list.songs[start:]):
      eta_display = (
        'eta {:0>8}'.format(str(timedelta(seconds=eta_seconds)))
        if eta_seconds else 'eta N/A'
      )
      real_index = i + start
      mprint(
        f'[{real_index + 1}/{total} '
        f'({eta_display})] Fetching {song}...'
      )

      start_time = time.time()
      if self.fetch_song(song):
        self.tag_song(song)
      end_time = time.time()

      duration_seconds = end_time - start_time
      total_download_seconds += duration_seconds
      average_download_seconds = round(total_download_seconds / (i + 1), 2)
      eta_seconds = average_download_seconds * (total - (real_index))

    mprint('Done!')
    mprint('Failed...')
    for song in self.failed:
      print(song.full_name)
    print()

  def fetch_song(self, song):
    path = os.path.join(TMP_DOWNLOAD_DIR, song.full_name)
    for attempt in range(RETRY_ATTEMPTS):
      ydl_opts = YDL_BASE_OPTS | {
        'outtmpl': f'{path}.%(ext)s',
      }
      try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
          ydl.download([f'ytsearch:{song.search_term}'])
        break
      except Exception as error:
        mprint(f'Issue: {error}')
        print(f'Retrying {attempt + 1}/{RETRY_ATTEMPTS}')
    else:
      mprint('Unable to fetch song. Skipping...')
      self.failed.append(song)
      return
    return True

  def tag_song(self, song):
    eyed3_file = eyed3.load(
      os.path.join(TMP_DOWNLOAD_DIR, song.file_name)
    )
    eyed3_file.tag.artist = song.artist
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
