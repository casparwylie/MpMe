import abc
import dataclasses
import json
import os
import platform
import shutil
import subprocess
import sys
import logging
import time
from datetime import timedelta

import eyed3
import yt_dlp
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logging.basicConfig(level=logging.INFO, format="%(message)s")


#################
### CONSTANTS ###
#################
PLATFORM = platform.platform()
MISC_ARTIST_NAME = "Other"
TMP_DOWNLOAD_DIR = "downloads"
AUDIO_FORMAT = "mp3"
IGNORE_DISKS = {"Macintosh HD"}
MAC_VOLUMES_DIR = "/Volumes"
LINUX_IOS_PATH = "/run/user/1000/gvfs"
IOS_MUSIC_PATH = "org.videolan.vlc-ios/Music"
LINUX_MOUNT_DIR = "/mnt"
LOCAL_EXPORT_DIR = "previous_downloads"
RETRY_ATTEMPTS = 3
IS_LINUX = "Linux" in PLATFORM
IS_MAC = "macOS" in PLATFORM
SONG_DELIM_CHAR = "~"
RESET_DOWNLOADS_EACH_RUN = True
DEBUG = False
TITLE_SHORT_CHARS = {"m", "s", "t"}
TAGGING_ENABLED = True
OFFER_EXPORT_FEATURES = True
UNSUPPORTED_OS_MSG = "Windows - not sure"
BIG_FILE_MB = 30
GDRIVE_FOLDER = "1MStG8IMb_qnzLjDOCSHThl4dD-F1XWF7"

###############
### HELPERS ###
###############


def format_title(string):
    string = string.strip().title()
    for char in TITLE_SHORT_CHARS:
        string = string.replace(f"'{char.upper()}", f"'{char}")
    return string


class YTDLogger:
    def debug(self, msg):
        logging.debug(msg)

    def info(self, msg):
        logging.info(msg)

    def warning(self, msg):
        logging.warning(msg)

    def error(self, msg):
        logging.error(msg)

def prepare():
    logging.info("Clearing downloads...")
    if os.path.exists(TMP_DOWNLOAD_DIR):
        if RESET_DOWNLOADS_EACH_RUN:
            shutil.rmtree(TMP_DOWNLOAD_DIR)
            os.mkdir(TMP_DOWNLOAD_DIR)
    else:
        os.mkdir(TMP_DOWNLOAD_DIR)


YDL_BASE_OPTS = {
    "format": "bestaudio/best",
    "logger": YTDLogger(),
    "postprocessors": [
        {
            "key": "FFmpegExtractAudio",
            "preferredcodec": AUDIO_FORMAT,
            "preferredquality": "192",
        }
    ],
    # May fix 403s according to https://stackoverflow.com/questions/32104702/youtube-dl-library-and-error-403-forbidden-when-using-generated-direct-link-by
    # 'cachedir': False,
}


@dataclasses.dataclass
class Song:
    yid: str | None
    name: str
    artist: str

    @property
    def search_term(self):
        if self.yid:
          return self.yid
        return (
            f"{self.name} {self.artist}"
            if self.artist != MISC_ARTIST_NAME
            else self.name
        )

    @property
    def full_name(self):
        return (
            f"{format_title(self.name)} {SONG_DELIM_CHAR} {format_title(self.artist)}"
        )

    @property
    def file_name(self):
        return f"{self.full_name}.{AUDIO_FORMAT}"

    @property
    def full_path(self):
        return os.path.join(TMP_DOWNLOAD_DIR, self.file_name)

    @property
    def size_mb(self):
        try:
          file_stats = os.stat(self.full_path)
          return file_stats.st_size / (1204 * 1204)
        except:
          return -1

    @classmethod
    def from_string(cls, string):
        string = string.strip(",").strip()
        match string.split(SONG_DELIM_CHAR):
          case (yid, name, artist):
            return cls(yid=yid, name=name, artist=artist)
          case (name, artist):
            return cls(yid=None, name=name, artist=artist)
          case _:
            raise Exception(f"Failed to parse: {string}.")

    def __str__(self):
        return self.full_name

    def tag(self):
        eyed3_file = eyed3.load(self.full_path)
        eyed3_file.tag.artist = self.artist
        eyed3_file.tag.title = self.name
        eyed3_file.tag.save()

    def fetch(self):
        path = os.path.join(TMP_DOWNLOAD_DIR, self.full_name)
        for attempt in range(RETRY_ATTEMPTS):
            ydl_opts = YDL_BASE_OPTS | {
                "outtmpl": f"{path}.%(ext)s",
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    result = ydl.download([f"ytsearch:{self.search_term}"])
                return True
            except Exception as error:
                logging.warning(f"Issue: {error}")
                logging.warning(f"Retrying {attempt + 1}/{RETRY_ATTEMPTS}")
        logging.warning("Unable to fetch song. Skipping...")
        return False


class SongList:
    def __init__(self):
        self.songs = []
        self.fetch_songs_failed = []
        self.fetch_songs_big = []

    def __str__(self):
        return "\n".join(str(song) for song in self.songs)

    def populate(self):
        options = (
            ("from file", self.populate_from_file),
            ("from json", self.populate_from_json),
            ("from URL", self.populate_from_url),
            ("manual", self.populate_from_input),
        )

        display_choices = " ".join(
            f"\n({index + 1}) {name}" for index, (name, _) in enumerate(options)
        )
        while True:
            choice = input(f"Please choose an option: {display_choices}\n:")
            try:
                self.songs = [
                    Song.from_string(line)
                    for line in sorted(options[int(choice) - 1][1]())
                    if line.strip()
                ]
                break
            except (IndexError, ValueError):
                pass

    def populate_from_file(self):
        """Excepts a linear list of Title~Artist"""
        with open(input("Enter file name path: ")) as data:
            return data.read().split("\n")

    def populate_from_json(self):
        """Expects a JSON object of "Artist": ["Title 1", ...]"""
        rows = []
        with open(input("Enter file name path: ")) as data:
            data = json.load(data)
            for artist, titles in data.items():
                for title in titles:
                    rows.append(f"{title}{SONG_DELIM_CHAR}{artist}")
            return rows

    def populate_from_url(self):
        raise Exception("Not implemented!")

    def populate_from_input(self):
        logging.info(
            "Paste a list of '<song>~<artist>' lines (Use ENTER then ctrl D when done): "
        )
        return sys.stdin.readlines()

    def show_big(self):
        if self.fetch_songs_big:
            logging.warning("Big files...")
            for song in self.fetch_songs_big:
                logging.warning(f"{round(song.size_mb, 2)}MB: {song.full_name}")

    def show_failed(self):
        if self.fetch_songs_failed:
            logging.warning("Failed...")
            for song in self.fetch_songs_failed:
                logging.warning(song.full_name)

    def fetch_all(self):
        self.fetch_songs_failed = []
        start = int(input("Start at index [0]: ") or 0)
        total = len(self.songs)
        average_download_seconds = 0
        total_download_seconds = 0
        eta_seconds = 0
        for i, song in enumerate(self.songs[start:]):
            real_index = i + start + 1
            eta_display = (
                "eta {}".format(str(timedelta(seconds=eta_seconds)))[:11]
                if eta_seconds
                else "eta N/A"
            )
            logging.info(f"[{real_index}/{total} " f"({eta_display})] Fetching {song}...")

            start_time = time.time()

            if song.fetch() and TAGGING_ENABLED:
                song.tag()
            else:
                self.fetch_songs_failed.append(song)
            if song.size_mb > BIG_FILE_MB:
              self.fetch_songs_big.append(song)

            end_time = time.time()

            duration_seconds = end_time - start_time
            total_download_seconds += duration_seconds
            average_download_seconds = round(total_download_seconds / (i + 1), 2)
            eta_seconds = average_download_seconds * (total - (real_index))

        logging.info("Done!")
        self.show_failed()
        self.show_big()


class Exporter(abc.ABC):
    @abc.abstractmethod
    def export(self, song_list):
        ...


class ExternalDiskExporter(Exporter):

    name = "MP3 Player (external disk)"

    def export(self):
        disk_name = self.find_disks()
        if IS_LINUX:
            subprocess.check_call(["mount", f"/dev/{disk_name}", LINUX_MOUNT_DIR])
            path = LINUX_MOUNT_DIR
        elif IS_MAC:
            path = os.path.join(MAC_VOLUMES_DIR, disk_name)
        else:
            raise Exception(UNSUPPORTED_OS_MSG)
        logging.info("Exporting songs to disk...")
        shutil.copytree(TMP_DOWNLOAD_DIR, path, dirs_exist_ok=True)

    def find_disks(self):
        while True:
            logging.info("Searching for disks...")
            if IS_LINUX:
                available_disks = {disk for disk in os.listdir("/dev") if "sd" in disk}
            elif IS_MAC:
                available_disks = set(os.listdir(MAC_VOLUMES_DIR))
            else:
                raise Exception(UNSUPPORTED_OS_MSG)
            available_disks = list(available_disks - IGNORE_DISKS)
            if not available_disks:
                time.sleep(1)
                continue
            display_disks = "\n".join(
                f"({index + 1}) {disk}" for index, disk in enumerate(available_disks)
            )
            try:
                choice = input(f"Which disk?\n {display_disks}\n:")
                return available_disks[int(choice) - 1]
            except (IndexError, ValueError):
                pass


class LocalExporter(Exporter):

    name = "Local Folder"

    def export(self):
        local_export_dir = LOCAL_EXPORT_DIR or input("Enter directory full path: ")
        if not os.path.exists(local_export_dir):
            logging.info("Folder does not exist. Creating.")
            os.mkdir(local_export_dir)
        try:
          shutil.copytree(TMP_DOWNLOAD_DIR, local_export_dir, dirs_exist_ok=True)
        except shutil.Error:
          # Apparently it's okay...
          # https://stackoverflow.com/questions/36564646/why-python-shutil-copytree-is-working-but-it-has-error
          pass


class IOSExporter(Exporter):

    name = "IOS Device"

    def export(self):
      if IS_LINUX:
        while True:
          logging.info("Searching for iOS device...")
          ios_device = None
          for directory in os.listdir(LINUX_IOS_PATH):
            if directory.startswith("afc"):
              ios_device = directory
              break
          if ios_device:
            break
          time.sleep(1)
        logging.info("Found device!")
        try:
          shutil.copytree(
            TMP_DOWNLOAD_DIR,
            os.path.join(LINUX_IOS_PATH, ios_device, IOS_MUSIC_PATH),
            dirs_exist_ok=True,
          )
        except shutil.Error:
          # Apparently it's okay...
          # https://stackoverflow.com/questions/36564646/why-python-shutil-copytree-is-working-but-it-has-error
          pass
      else:
        raise Exception(UNSUPPORTED_OS_MSG)

class GoogleDriveExporter(Exporter):

    name = "Google Drive"
    api_key_file = ".mpme-google-key.json"
    token_file = ".gcptoken.json"
    client = None

    def export(self):
      if not (client := self.auth()):
        return
      self.client = client
      for filename in os.listdir(TMP_DOWNLOAD_DIR):
        logging.info(f"Uploading {filename}")
        self.upload_file(filename)

    def auth(self):
      creds = None
      scopes = ["https://www.googleapis.com/auth/drive.file"]
      if os.path.exists(self.token_file):
          creds = Credentials.from_authorized_user_file(self.token_file, scopes)
      if not creds or not creds.valid:
          if creds and creds.expired and creds.refresh_token:
              creds.refresh(Request())
          else:
              flow = InstalledAppFlow.from_client_secrets_file(self.api_key_file, scopes)
              creds = flow.run_local_server(port=0)
          with open(self.token_file, 'w') as token_file:
              token_file.write(creds.to_json())
      return build('drive', 'v3', credentials=creds)

    def upload_file(self, filename):
      file_metadata = {
          "name": filename,
          "parents": [GDRIVE_FOLDER]
      }
      media = MediaFileUpload(
        os.path.join(TMP_DOWNLOAD_DIR, filename),
        mimetype=f"audio/{AUDIO_FORMAT}"
      )
      self.client.files().create(
          body=file_metadata,
          media_body=media,
          fields='id'
      ).execute()




EXPORTERS = (
    LocalExporter,
    ExternalDiskExporter,
    GoogleDriveExporter,
    IOSExporter,
)


def offer_exports():
    if not OFFER_EXPORT_FEATURES:
      return
    for exporter in EXPORTERS:
        if input(f"Export to {exporter.name} [y/N]: ") == "y":
            exporter().export()
            logging.info("Done!")


############
### MAIN ###
############


def show_introduction():
    logging.info(
        """
** Welcome to MPME! **
Scan, download and export music to an external source.
"""
    )


def main():
    show_introduction()

    if '--export' not in sys.argv:
      prepare()
      song_list = SongList()
      song_list.populate()
      song_list.fetch_all()

    offer_exports()

    logging.info("Finished. Quitting.")


if __name__ == "__main__":
    main()
