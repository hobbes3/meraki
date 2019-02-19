API_KEY = "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
ORG_ID = 999999

# Size of each log file.
# 1 MB = 1 * 1024 * 1024
LOG_ROTATION_BYTES = 25 * 1024 * 1024
# Maximum number of log files.
LOG_ROTATION_LIMIT = 100

TIMEOUT = 5

# set_syslog_servers.py
SET_SYSLOG_SERVERS_LOG_PATH = "/home/splunk/Splunk_TA_meraki_logs/set_syslog_servers.log"
SYSLOG_HOST = "1.2.3.4"
SYSLOG_PORT = 514

# get_data.py
GET_DATA_LOG_PATH = "/home/splunk/Splunk_TA_meraki_logs/get_data.log"
THREADS = 4
SLEEP = 1
HEC_URL = "https://1.2.3.4:8088"
HEC_TOKEN = "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
