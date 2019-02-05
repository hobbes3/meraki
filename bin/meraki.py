#!/usr/bin/env python
# hobbes3

import requests
import os
import sys
import time
import json
import logging
import logging.handlers
from pathlib import Path

from settings import *

if __name__ == "__main__":
    start_time = time.time()

    setting_file = Path(os.path.dirname(os.path.realpath(__file__)) + "/settings.py")

    if not os.path.exists(setting_file):
        sys.exit("The config file, settings.py, doesn't exist! Please copy, edit, and rename default_settings.py to settings.py.")

    logger = logging.getLogger("logger")
    logger.setLevel(logging.DEBUG)
    handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=LOG_ROTATION_BYTES, backupCount=LOG_ROTATION_LIMIT)
    handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)-7s] (%(threadName)-10s) %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)

    print("Log file at {}.".format(LOG_PATH))

    headers = {
        "X-Cisco-Meraki-API-Key": API_KEY,
        "Content-Type": "application/json",
    }

    url = "https://api.meraki.com/api/v0/organizations/{}/networks".format(ORG_ID)

    r = requests.get(url, headers=headers)
    networks = r.json()

    syslog_roles = [
        "Flows",
        "URLs",
        "Security events",
        "Appliance event log",
        #"Wireless event log",
        #"Air Marshal events",
        #"Switch event log",
        #"IDS alerts",
    ]

    logger.info("Found {} networks.".format(len(networks)))

    for network in networks:
        network_name = network["name"]
        network_id = network["id"]

        url = "https://api.meraki.com/api/v0/networks/{}/syslogServers".format(network_id)

        r = requests.get(url, headers=headers)
        servers = r.json()

        logger.info("{} ({})".format(network_name, network_id))

        if servers:
            servers = [
                s
                for s in servers
                if s["host"]!="54.224.32.118" and s["host"]!=SYSLOG_HOST
            ]

        servers.append({
            "host": SYSLOG_HOST,
            "port": SYSLOG_PORT,
            "roles": syslog_roles,
        })

        data = {"servers": servers}

        r = requests.put(url, headers=headers, json=data)
        status_code = r.status_code

        if status_code == 200:
            logger.info("Status code {}: Added {}:{} syslog with {}.".format(status_code, SYSLOG_HOST, SYSLOG_PORT, syslog_roles))
        else:
            logger.error("Status code {}: Not added {}:{} syslog with {}.".format(status_code, SYSLOG_HOST, SYSLOG_PORT, syslog_roles))
