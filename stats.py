import dataclasses
import mpme
import os


DISPLAY_ARTIST_COUNT = 50


def load_songs():
  disk_name = mpme.ExternalDiskExporter().find_disks()
  path = os.path.join(mpme.VOLUMES_DIR, disk_name)
  return [
    mpme.Song.from_file(os.path.join(path, song))
    for song in os.listdir(path)
    if song.endswith(mpme.AUDIO_FORMAT)
  ]

def get_stats(songs):
  print(f'\nTotal: {len(songs)} \n')
  artist_song_map = {}
  for song in songs:
    if song.artist not in artist_song_map:
      artist_song_map[song.artist] = []
    artist_song_map[song.artist].append(song.name)

  sorted_data = sorted(
    artist_song_map.items(),
    key=lambda item: len(item[1]),
    reverse=True)
  for artist, songs in sorted_data[:DISPLAY_ARTIST_COUNT]:
    print(f'{artist}: {len(songs)} songs')

def main():
  songs = load_songs()
  get_stats(songs)


if __name__ == '__main__':
  main()
