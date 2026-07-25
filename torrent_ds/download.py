import os
import shutil
import logging
import tempfile
from datetime import datetime
from calendar import monthrange
from ncoreparser import Client as NcoreClient
from ncoreparser import (
    SearchParamType,
    NcoreCredentialError,
    NcoreConnectionError,
    NcoreDownloadError,
    NcoreParserError,
    Size
)

from torrent_ds.data import create_session, Torrent, SeenTorrent
from torrent_ds.creds import Credential
from torrent_ds.torrent_client import BaseTorrentClient


download_categories = {
    "movies": [
        SearchParamType.SD_HUN,
        SearchParamType.SD,
        SearchParamType.DVD_HUN,
        SearchParamType.DVD,
        SearchParamType.DVD9_HUN,
        SearchParamType.DVD9,
        SearchParamType.HD_HUN,
        SearchParamType.HD
    ], "series": [
        SearchParamType.SDSER_HUN,
        SearchParamType.SDSER,
        SearchParamType.DVDSER_HUN,
        SearchParamType.DVDSER,
        SearchParamType.HDSER_HUN,
        SearchParamType.HDSER
    ], "musics": [
        SearchParamType.MP3_HUN,
        SearchParamType.MP3,
        SearchParamType.LOSSLESS_HUN,
        SearchParamType.LOSSLESS
    ], "clips": [
        SearchParamType.CLIP,
    ], "games": [
        SearchParamType.GAME_ISO,
        SearchParamType.GAME_RIP,
        SearchParamType.CONSOLE
    ], "books": [
        SearchParamType.EBOOK_HUN,
        SearchParamType.EBOOK
    ], "programs": [
        SearchParamType.ISO,
        SearchParamType.MISC,
        SearchParamType.MOBIL
    ], "xxx": [
        SearchParamType.XXX_IMG,
        SearchParamType.XXX_SD,
        SearchParamType.XXX_DVD,
        SearchParamType.XXX_HD
    ]
}


class DownloadManager:
    def __init__(self, config):
        self._config = config
        self._logger = logging.getLogger("torrent-ds")

    def _get_tracker_client(self, credential_title):
        cred = Credential(credential_title)
        try:
            client = NcoreClient(timeout=2)
            client.login(cred.username, cred.password)

        except NcoreCredentialError:
            self._logger.error("Bad credential for label: '{}'.".format(cred.label))
            return None
        except NcoreConnectionError:
            self._logger.warning("Connection error with tracker.")
            return None
        except NcoreParserError as e:
            self._logger.warning("Error while parsing web page. {}".format(e))
            return None
        return client

    def _get_download_path(self, torrent, label):
        for path in download_categories:
            for t_type in download_categories[path]:
                if torrent["type"] == t_type:
                    full_path = self._config[label].get(path)
                    return full_path if full_path else None

    def _get_config_list(self, label, name):
        value = self._config[label].get(name)
        if value:
            return [item.strip() for item in value.split(";") if item.strip()]
        return []

    def _reached_limit(self, db_session, label):
        # Get number of torrents for label in the target month
        # Returns True if reached the limit
        now = datetime.now()
        last_day = monthrange(now.year, now.month)[1]
        start_date = datetime(year=now.year, month=now.month, day=1)
        end_date = datetime(year=now.year, month=now.month, day=last_day)
        torrents = db_session.query(Torrent).filter(Torrent.date.between(start_date, end_date)) \
                                            .filter(Torrent.label.contains(label)).count()
        limit = self._config[label].get("limit")
        if limit:
            limit = int(limit)
            if torrents >= limit:
                self._logger.info("The download limit is reached. Label: {}, limit: {}/{}".format(label, torrents, limit))
                return True
        return False

    def _add_torrent(self, torrent, tracker_client, torrent_client, label):
        if tracker_client is None:
            return
        db_session = create_session()

        already_seen = db_session.query(SeenTorrent).filter(SeenTorrent.tracker_id == torrent["id"]).count() != 0
        if already_seen:
            db_session.close()
            return

        if self._reached_limit(db_session, label):
            db_session.close()
            return

        tmp_dir = tempfile.mkdtemp()
        os.chmod(tmp_dir, 0o777)
        try:
            file_path = tracker_client.download(torrent, tmp_dir)
            d_path = self._get_download_path(torrent, label)
            if d_path:
                download_dir = os.path.abspath(d_path)
            else:
                d_path = "default download dir."
                download_dir = None
            client_id = torrent_client.add_torrent(file_path, download_dir, torrent)
            torrent_db = Torrent()
            torrent_db.tracker_id = torrent["id"]
            torrent_db.client_id = client_id
            torrent_db.title = torrent["title"]
            torrent_db.label = label
            db_session.add(torrent_db)
            seen = SeenTorrent()
            seen.tracker_id = torrent["id"]
            db_session.add(seen)
            db_session.commit()
            self._logger.info("Download torrent: '{}' to '{}'.".format(torrent['title'], d_path))
        except NcoreConnectionError:
            self._logger.warning("Unable to connect to tracker while"
                                 " downloading '{}'.".format(torrent['title']))
        except NcoreDownloadError as e:
            self._logger.warning(e.args[0])
        except NcoreParserError as e:
            self._logger.warning("Error while parsing web page. {}".format(e))
        finally:
            shutil.rmtree(tmp_dir)
            db_session.close()

    def clean_db(self):
        client = BaseTorrentClient.create(self._config)
        if client is None:
            return

        try:
            client_ids = client.get_torrent_ids()
        except Exception: # Error while get torrents (timeout)
            self._logger.warning("Unable to clean database (time out)")
            return
        deleted_cnt = 0
        db_session = create_session()
        db_torrents = db_session.query(Torrent).all()
        for item in db_torrents:
            if str(item.client_id) not in client_ids:
                db_session.delete(item)
                deleted_cnt += 1
        db_session.commit()
        self._logger.info("Cleaned {} items from db.".format(deleted_cnt))

        db_session.close()

    def download_rss(self):
        torrent_client = BaseTorrentClient.create(self._config)
        if torrent_client is None:
            return
        rss_list = [rss for rss in self._config.sections() if rss.startswith("rss")]
        for rss in rss_list:
            url = self._config[rss]["url"]
            credential = self._config[rss]["credential"]
            self._logger.info("Get torrents from rss: '{}', label: '{}'.".format(url, rss))

            tracker_client = self._get_tracker_client(credential)
            if tracker_client is None:
                return

            try:
                torrents = tracker_client.get_by_rss(url)
            except NcoreConnectionError:
                self._logger.warning("Unable to connect to tracker, "
                                     "while get rss.")
                return
            except NcoreParserError as e:
                self._logger.warning("Error while parsing web page. {}".format(e))
                return

            for torrent in torrents:
                self._add_torrent(torrent, tracker_client, torrent_client, rss)
            tracker_client.logout()

    def _has_enough_free_space(self, torrent, keep_free_size):
        if keep_free_size is None:
            return True
        d_path = self._get_download_path(torrent, "recommended")
        if d_path is None:
            return True
        download_dir = os.path.abspath(d_path)
        if not os.path.exists(download_dir):
            return True
        torrent_size_bytes = torrent['size'].bytes
        free_bytes = shutil.disk_usage(download_dir).free
        if (free_bytes - torrent_size_bytes) >= keep_free_size.bytes:
            return True
        self._logger.info(
            "Skipping torrent '{}' (size: {}): insufficient free space "
            "(free: {:.2f} GiB, keep free: {}).".format(
                torrent['title'],
                torrent['size'],
                free_bytes / 1024**3,
                keep_free_size
            )
        )
        return False

    def download_recommended(self):
        tracker_client = self._get_tracker_client(self._config["recommended"]["credential"])
        if tracker_client is None:
            return
        torrent_client = BaseTorrentClient.create(self._config)
        if torrent_client is None:
            return
        size_cfg = self._config["recommended"].get("max_size")
        max_size = Size(size_cfg) if size_cfg else None
        keep_free_cfg = self._config["recommended"].get("keep_free_space")
        keep_free_size = Size(keep_free_cfg) if keep_free_cfg else None
        max_count_cfg = self._config["recommended"].get("max_count")
        max_count = int(max_count_cfg) if max_count_cfg else None
        self._logger.info("Downloading recommended...")
        categories = self._get_config_list("recommended", "categories")
        for category in categories:
            types = download_categories.get(category)
            if types is None:
                self._logger.warning("Unknown category: '{}'.".format(category))
                return
            for type in types:
                try:
                    torrents = tracker_client.get_recommended(type)
                except NcoreConnectionError:
                    self._logger.warning("Unable to connect to tracker,"
                                         " while getting recommended.")
                    continue
                except NcoreParserError as e:
                    self._logger.warning("Error while parsing web page. {}".format(e))
                    continue
                added = 0
                for torrent in torrents:
                    if max_count and added >= max_count:
                        self._logger.info("Reached recommended max_count limit ({}).".format(max_count))
                        break
                    if max_size and torrent['size'] > max_size:
                        self._logger.info("Skipping torrent '{}', it is too large: '{}'.".format(torrent['title'],
                                                                                                 torrent['size']))
                        continue
                    if not self._has_enough_free_space(torrent, keep_free_size):
                        continue
                    self._add_torrent(torrent, tracker_client, torrent_client, "recommended")
                    added += 1
        tracker_client.logout()

    def download_hitnrun(self):
        tracker_client = self._get_tracker_client(self._config["hitnrun"]["credential"])
        if tracker_client is None:
            return
        torrent_client = BaseTorrentClient.create(self._config)
        if torrent_client is None:
            return

        self._logger.info("Downloading hitnrun activity...")
        try:
            torrents = tracker_client.get_by_activity()
        except NcoreConnectionError:
            self._logger.warning("Unable to connect to tracker, while getting hitnrun activity.")
            return
        except NcoreParserError as e:
            self._logger.warning("Error while parsing web page. {}".format(e))
            return

        for torrent in torrents:
            if self._get_download_path(torrent, "hitnrun"):
                self._add_torrent(torrent, tracker_client, torrent_client, "hitnrun")
        tracker_client.logout()

    def start_all(self):
        client = BaseTorrentClient.create(self._config)
        if client is None:
            return
        self._logger.info("Starting all torrents.")
        client.start_all()

    def stop_all(self):
        client = BaseTorrentClient.create(self._config)
        if client is None:
            return
        self._logger.info("Stopping all torrents.")
        client.stop_all()
