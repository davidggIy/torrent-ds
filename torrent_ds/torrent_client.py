import logging
import os
from abc import ABC, abstractmethod

import qbittorrentapi
from transmissionrpc import Client as TransmissionRpcClient

from torrent_ds.creds import Credential


class BaseTorrentClient(ABC):
    def __init__(self, config):
        self._config = config
        self._logger = logging.getLogger("torrent-ds")

    @classmethod
    def create(cls, config):
        return create_torrent_client(config)

    @abstractmethod
    def connect(self):
        pass

    @abstractmethod
    def add_torrent(self, file_path, download_dir=None, torrent=None):
        pass

    @abstractmethod
    def get_torrent_ids(self):
        pass

    @abstractmethod
    def start_all(self):
        pass

    @abstractmethod
    def stop_all(self):
        pass


class TransmissionTorrentClient(BaseTorrentClient):
    def __init__(self, config):
        super().__init__(config)
        self._client = None

    def connect(self):
        params = {}
        address = self._config["transmission"].get("ip_address")
        if address:
            params["address"] = address
        port = self._config["transmission"].get("port")
        if port:
            params["port"] = port
        if _get_bool(self._config["transmission"], "authenticate"):
            cred = Credential("transmission")
            params["user"] = cred.username
            params["password"] = cred.password

        try:
            self._client = TransmissionRpcClient(**params)
        except Exception as e:
            self._logger.error("Error while connecting to transmission-rpc. {}.".format(e))
            return False
        return True

    def add_torrent(self, file_path, download_dir=None, torrent=None):
        args = {}
        if download_dir:
            args["download_dir"] = os.path.abspath(download_dir)
        new_torrent = self._client.add_torrent(file_path, **args)
        return str(new_torrent.id)

    def get_torrent_ids(self):
        return [str(torrent.id) for torrent in self._client.get_torrents()]

    def start_all(self):
        if len(self._client.get_torrents()) == 0:
            return
        self._client.start_all()

    def stop_all(self):
        ids = [torrent.id for torrent in self._client.get_torrents()]
        if len(ids) > 0:
            self._client.stop_torrent(ids)


class QBittorrentTorrentClient(BaseTorrentClient):
    def __init__(self, config):
        super().__init__(config)
        self._client = None

    def connect(self):
        params = {}
        host = self._config["qbittorrent"].get("host")
        if host:
            params["host"] = host
        port = self._config["qbittorrent"].get("port")
        if port:
            params["port"] = port
        authenticate = _get_bool(self._config["qbittorrent"], "authenticate")
        if authenticate:
            cred = Credential("qbittorrent")
            params["username"] = cred.username
            params["password"] = cred.password

        try:
            self._client = qbittorrentapi.Client(**params)
            if authenticate:
                self._client.auth_log_in()
            else:
                self._client.app_version()
        except Exception as e:
            self._logger.error("Error while connecting to qBittorrent. {}.".format(e))
            return False
        return True

    def add_torrent(self, file_path, download_dir=None, torrent=None):
        args = {"torrent_files": file_path}
        if download_dir:
            args["save_path"] = os.path.abspath(download_dir)
        self._client.torrents_add(**args)
        return self._find_torrent_id(file_path, torrent)

    def _find_torrent_id(self, file_path, torrent):
        torrent_name = torrent["title"] if torrent else os.path.splitext(os.path.basename(file_path))[0]
        for torrent in self._client.torrents_info():
            if torrent.name == torrent_name:
                return torrent.hash
        return torrent_name

    def get_torrent_ids(self):
        ids = []
        for torrent in self._client.torrents_info():
            ids.append(torrent.hash)
            ids.append(torrent.name)
        return ids

    def start_all(self):
        self._client.torrents_resume(torrent_hashes="all")

    def stop_all(self):
        self._client.torrents_pause(torrent_hashes="all")


def create_torrent_client(config):
    client_type = config.get("torrent_client", "type", fallback="transmission").lower()
    if client_type == "transmission":
        client = TransmissionTorrentClient(config)
    elif client_type == "qbittorrent":
        client = QBittorrentTorrentClient(config)
    else:
        raise ValueError("Unknown torrent client type: '{}'.".format(client_type))

    if not client.connect():
        return None
    return client


def _get_bool(section, key):
    return section.get(key, fallback="False").lower() == "true"
