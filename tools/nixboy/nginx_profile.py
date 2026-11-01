"""Fixed operator-owned native nginx fixture and observer origins.

This is deliberately not an executable topology language. Both clients are real
Linux network namespaces with their own veth/IP; QEMU's loopback forward is not
used as policy evidence.
"""
SERVER = "10.77.0.1"
PORT = 8080
HOST = "lesson.nixboy.invalid"
HOST_ID = "nixboy-native-v1"
ORIGINS = {"allowed": {"namespace": "nb-client-allowed", "address": "10.77.0.2"},
           "denied": {"namespace": "nb-client-denied", "address": "10.77.0.3"}}
PREPARATIONS = ("nginx-policy-v1", "nginx-reload-v1")
PUBLIC = "native nginx public fixture v1\n"
STAFF = "native nginx staff protected data v1\n"
READY = "native nginx stage two ready v1\n"
PENDING = "native nginx stage two pending\n"
DENIED = "native nginx policy denied\n"
WRONG_HOST = "native nginx wrong host\n"


def configuration(preparation, solved=False):
    if preparation not in PREPARATIONS:
        raise ValueError("unknown native nginx preparation")
    restricted = solved or preparation == "nginx-reload-v1"
    ready = solved or preparation == "nginx-policy-v1"
    policy = "allow 10.77.0.2; deny all;" if restricted else "allow all;"
    status, body = (200, READY) if ready else (503, PENDING)
    wrong, denied, response = (text.replace("\n", "\\n") for text in (WRONG_HOST, DENIED, body))
    return f'''user www-data;
worker_processes 1;
pid /run/nginx.pid;
error_log /var/log/nginx/error.log notice;
events {{ worker_connections 128; }}
http {{
  default_type text/plain;
  log_format probe escape=json '{{"probe":"$http_x_nixboy_probe","source":"$remote_addr","host":"$host","status":$status,"request":"$request_uri"}}';
  access_log /var/log/nginx/nixboy-access.jsonl probe;
  server {{
    listen {SERVER}:{PORT} default_server;
    server_name _;
    return 421 "{wrong}";
  }}
  server {{
    listen {SERVER}:{PORT};
    server_name {HOST};
    add_header X-Nixboy-Host {HOST_ID} always;
    error_page 403 =403 /denied.txt;
    location = /public.txt {{ alias /srv/nixboy-nginx/public.txt; }}
    location = /staff.txt {{ {policy} alias /srv/nixboy-nginx/staff.txt; }}
    location = /denied.txt {{ internal; return 403 "{denied}"; }}
    location = /stage2 {{ return {status} "{response}"; }}
  }}
}}
'''
