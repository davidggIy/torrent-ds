import sys
import logging
import time
import inspect
import threading
from datetime import datetime

import torrent_ds.error
from torrent_ds.data import global_init as db_init
from torrent_ds.logger import init_logger
from torrent_ds.download import DownloadManager
from torrent_ds.config import load_config
from torrent_ds.util import (
    check_time,
    check_between_time,
    check_sleep_day
)

def main():
    # Initialize logger
    init_logger()

    # Load configuration
    _, config = load_config()

    logger = logging.getLogger("torrent-ds")
    try:
        db_init()

        if config["recommended"].get("enable") != "True":
            logger.info("Recommended function is disabled. Skip.")
        if not config.has_section("hitnrun") or config["hitnrun"].get("enable") != "True":
            logger.info("Hitnrun function is disabled. Skip.")

        start_time = datetime.now()
        start_time_recommended = datetime.min
        start_time_hitnrun = datetime.min
        download_manager = DownloadManager(config)
        recommended_thread = None
        hitnrun_thread = None
        # state of torrents in the configured torrent client
        started = True
        while True:

            if check_time(start_time, seconds=int(config["download"]["retry_interval"])):
                download_manager.clean_db()
                download_manager.download_rss()
                start_time = datetime.now()

            if config["recommended"].get("enable") == "True":
                thread_idle = recommended_thread is None or not recommended_thread.is_alive()
                if thread_idle and check_time(start_time_recommended, hours=int(config["recommended"]["retry_interval"])):
                    recommended_thread = threading.Thread(
                        target=download_manager.download_recommended, daemon=True)
                    recommended_thread.start()
                    start_time_recommended = datetime.now()

            if config.has_section("hitnrun") and config["hitnrun"].get("enable") == "True":
                thread_idle = hitnrun_thread is None or not hitnrun_thread.is_alive()
                if thread_idle and check_time(start_time_hitnrun, hours=int(config["hitnrun"]["retry_interval"])):
                    hitnrun_thread = threading.Thread(
                        target=download_manager.download_hitnrun, daemon=True)
                    hitnrun_thread.start()
                    start_time_hitnrun = datetime.now()

            sleep_days = config.get("torrent_client", "sleep_days", fallback=config["transmission"].get("sleep_days"))
            sleep_time = config.get("torrent_client", "sleep_time", fallback=config["transmission"].get("sleep_time"))
            if sleep_time and sleep_days:
                if (check_between_time(sleep_time.split('-')[0], sleep_time.split('-')[1])
                    and check_sleep_day(sleep_days.split(';'))):
                    if started:
                        download_manager.stop_all()
                        started = False
                else:
                    if not started:
                        download_manager.start_all()
                        started = True

            time.sleep(1)

    except KeyboardInterrupt:
        logger.info("Exiting application.")
        sys.exit(0)

    except Exception as e:
        for _, obj in inspect.getmembers(torrent_ds.error):
            if inspect.isclass(obj) and isinstance(e, obj):
                sys.exit(1)
        logger.exception("Unhandled exception: {}".format(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
