import abc
import dataclasses
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from datetime import timedelta

import eyed3
import yt_dlp

#################
### CONSTANTS ###
#################
PLATFORM = platform.platform()
MISC_ARTIST_NAME = "Other"
TMP_DOWNLOAD_DIR = "downloads"
AUDIO_FORMAT = "mp3"
IGNORE_DISKS = {"Macintosh HD"}
MAC_VOLUMES_DIR = "/Volumes"
LINUX_MOUNT_DIR = "/mnt"
BACKUP_DIR = "backup"
RETRY_ATTEMPTS = 3
MAC_PLATFORM_PART = "macOS"
LINUX_PLATFORM_PART = "Linux"
SONG_DELIM_CHAR = "~"
RESET_DOWNLOADS_EACH_RUN = True
DEBUG = False
TITLE_SHORT_CHARS = {"m", "s", "t"}
TAGGING_ENABLED = True
OFFER_EXPORT_FEATURES = False
UNSUPPORTED_OS_MSG = "Windows - not sure"
BIG_FILE_MB = 30

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
        if DEBUG:
            print(msg)

    def info(self, msg):
        if DEBUG:
            print(msg)

    def warning(self, msg):
        print(msg)

    def error(self, msg):
        print(msg)


def mprint(message):
    print(f"\n{message}\n")


def prepare():
    print("Clearing downloads...")
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
            "preferredcodec": "mp3",
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
                mprint(f"Issue: {error}")
                print(f"Retrying {attempt + 1}/{RETRY_ATTEMPTS}")
        mprint("Unable to fetch song. Skipping...")
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
        mprint(
            "Paste a list of '<song>~<artist>' lines (Use ENTER then ctrl D when done): "
        )
        return sys.stdin.readlines()

    def show_big(self):
        if self.fetch_songs_big:
            mprint("Big files...")
            for song in self.fetch_songs_big:
                print(f"{round(song.size_mb, 2)}MB: {song.full_name}")
            print()

    def show_failed(self):
        if self.fetch_songs_failed:
            mprint("Failed...")
            for song in self.fetch_songs_failed:
                print(song.full_name)
            print()

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
            mprint(f"[{real_index}/{total} " f"({eta_display})] Fetching {song}...")

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

        mprint("Done!")
        self.show_failed()
        self.show_big()


class Exporter(abc.ABC):
    @abc.abstractmethod
    def export(self):
        ...


class ExternalDiskExporter(Exporter):

    name = "MP3 Player (external disk)"

    def export(self):
        disk_name = self.find_disks()
        if LINUX_PLATFORM_PART in PLATFORM:
            subprocess.check_call(["mount", f"/dev/{disk_name}", LINUX_MOUNT_DIR])
            path = LINUX_MOUNT_DIR
        elif MAC_PLATFORM_PART in PLATFORM:
            path = os.path.join(MAC_VOLUMES_DIR, disk_name)
        else:
            raise Exception(UNSUPPORTED_OS_MSG)
        print("Exporting songs to disk...")
        shutil.copytree(TMP_DOWNLOAD_DIR, path, dirs_exist_ok=True)

    def find_disks(self):
        mprint("Searching for disks...")
        while True:
            if LINUX_PLATFORM_PART in PLATFORM:
                available_disks = {disk for disk in os.listdir("/dev") if "sd" in disk}
            elif MAC_PLATFORM_PART in PLATFORM:
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

    name = "Local (backup)"

    def export(self):
        if not os.path.exists(BACKUP_DIR):
            os.mkdir(BACKUP_DIR)
        shutil.copytree(TMP_DOWNLOAD_DIR, BACKUP_DIR, dirs_exist_ok=True)


class GoogleDriveExporter(Exporter):

    name = "Google Drive"

    def export(self):
        raise Exception("Not implemented!")


EXPORTERS = (
    ExternalDiskExporter,
    LocalExporter,
    GoogleDriveExporter,
)


def offer_exports():
    if OFFER_EXPORT_FEATURES:
        for exporter in EXPORTERS:
            if input(f"Export to {exporter.name} [y/N]: ") == "y":
                exporter().export()
                mprint("Done!")


############
### MAIN ###
############


def show_introduction():
    print(
        """
** Welcome to MPME! **
Scan, download and export music to an external source.
"""
    )


def main():
    show_introduction()
    prepare()

    song_list = SongList()
    song_list.populate()
    song_list.fetch_all()

    offer_exports()

    mprint("Finished. Quitting.")


if __name__ == "__main__":
    main()
