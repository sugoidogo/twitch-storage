from configparser import ConfigParser
import os
from http.server import ThreadingHTTPServer,BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlencode,parse_qsl,urlparse
from urllib.request import Request,urlopen,HTTPError
import json
from traceback import print_exc
from subprocess import run
import mimetypes

config=ConfigParser()
config['network']={
    'ip':'localhost',
    'port':'9123',
    'tba_url':''
}
config['storage']={
    'root':'data'
}
config['api']={
    'client_id':'',
    'client_secret':'',
    'redirect_uri':''
}
config['limits']={
    '0000':50,
    '1000':500,
    '2000':1000,
    '3000':2500
}
config_path='ts.ini'
config_file=os.fdopen(os.open(config_path, os.O_RDWR|os.O_CREAT),'r+')
config.read_file(config_file)
config_file.seek(0)
config.write(config_file)
config_file.close()

def write_config():
    with open(config_path,'w') as config_file:
        config.write(config_file)

def get_broadcaster_id():
    headers={
        'Client-ID':config['api']['client_id'],
        'Authorization':'Bearer '+config['api']['access_token']
    }
    url='https://api.twitch.tv/helix/users'
    request=Request(url,headers=headers)
    response=json.loads(urlopen(request).read().decode())
    config['api']['broadcaster_id']=response['data'][0]['id']

def request_auth():
    print('Visit the url below to authorize ts to check subscriptions')
    query={
        'response_type':'code',
        'scope':'channel:read:subscriptions',
        'client_id':config['api']['client_id']
    }
    location='https://id.twitch.tv/oauth2/authorize?'
    location+=urlencode(query)
    location+='&redirect_uri='+config['api']['redirect_uri']
    print(location)

def refresh_tokens():
    try:
        print('refreshing access token')
        query={
            'refresh_token':config['api']['refresh_token'],
            'client_id':config['api']['client_id'],
            'client_secret':config['api']['client_secret'],
            'grant_type':'refresh_token'
        }
        url='https://id.twitch.tv/oauth2/token'
        query=urlencode(query)
        query+='&redirect_uri='+config['api']['redirect_uri']
        request=Request('https://id.twitch.tv/oauth2/token',query.encode(),method='POST')
        response=json.loads(urlopen(request).read().decode())
        config['api']['access_token']=response['access_token']
        config['api']['refresh_token']=response['refresh_token']
        write_config()
    except HTTPError as error:
        request_auth()
        raise error

def get_tokens(code):
    query={
        'code':code,
        'client_id':config['api']['client_id'],
        'client_secret':config['api']['client_secret'],
        'grant_type':'authorization_code'
    }
    url='https://id.twitch.tv/oauth2/token'
    query=urlencode(query)
    query+='&redirect_uri='+config['api']['redirect_uri']
    request=Request('https://id.twitch.tv/oauth2/token',query.encode(),method='POST')
    response=json.loads(urlopen(request).read().decode())
    config['api']['access_token']=response['access_token']
    config['api']['refresh_token']=response['refresh_token']

def get_sub(user_id):
    headers={
        'client-id':config['api']['client_id'],
        'Authorization':'Bearer '+config['api']['access_token']
    }
    query={
        'broadcaster_id':config['api']['broadcaster_id'],
        'user_id':user_id
    }
    url='https://api.twitch.tv/helix/subscriptions?'
    url+=urlencode(query)
    request=Request(url,headers=headers)
    try:
        response=json.loads(urlopen(request).read().decode())
        if len(response['data']) == 0:
            return '0000'
        return response['data'][0]['tier']
    except HTTPError:
        print_exc()
        refresh_tokens()
        return get_sub(user_id)

def get_validation(authorization):
    request=Request('https://id.twitch.tv/oauth2/validate',headers={'Authorization':authorization})
    response=urlopen(request)
    validation=json.loads(response.read())
    return validation

class TS(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def do_GET(self):
        try:
            if self.path.startswith('/code'):
                query=dict(parse_qsl(urlparse(self.path).query))
                self.send_response(200)
                self.end_headers()
                get_tokens(query['code'])
                get_broadcaster_id()
                return write_config()
            validation=get_validation(self.headers.get('Authorization'))
            if 'user_id' not in validation or 'client_id' not in validation:
                #print('unauthorized client')
                return self.send_error(401)
            path=Path(config['storage']['root'])
            path=path.joinpath(validation['user_id'])
            path=path.joinpath(validation['client_id'])
            while self.path[0] == '/' or self.path[0] == '\\':
                self.path=self.path[1:]
            path=path.joinpath(self.path)
            print(self.requestline+' > '+str(path))
            if not path.exists():
                return self.send_error(404)
            if path.is_dir():
                files=os.listdir(path)
                response=json.dumps(files).encode()
                self.send_response(200)
                self.send_header('Content-Length', len(response))
                self.end_headers()
                return self.wfile.write(response)
            content_type,content_encoding=mimetypes.guess_type(path)
            data=path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Length', len(data))
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Encoding',content_encoding)
            self.end_headers()
            self.wfile.write(data)
        except HTTPError as error:
            print_exc()
            self.send_error(error.code)
        except:
            self.send_error(500)
            print_exc()
    
    def do_POST(self):
        try:
            validation=get_validation(self.headers.get('Authorization'))
            path=Path(config['storage']['root'])
            path=path.joinpath(validation['user_id'])
            path.mkdir(parents=True,exist_ok=True)
            usage=sum(file.stat().st_size for file in path.rglob('*'))
            usage+=int(self.headers['Content-Length'])
            path=path.joinpath(validation['client_id'])
            while self.path[0] == '/' or self.path[0] == '\\':
                self.path=self.path[1:]
            path=path.joinpath(self.path)
            print(self.requestline+' > '+str(path))
            if path.exists():
                usage-=path.stat().st_size
            usage=int(usage/1024/1024)
            if validation['user_id'] in config['limits']:
                limit=config['limits'][validation['user_id']]
            else:
                limit=int(config['limits'][get_sub(validation['user_id'])])
            if usage > limit:
                return self.send_error(413)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(204)
            self.send_header('content-length',0)
            self.end_headers()
        except HTTPError as error:
            print_exc()
            self.send_error(error.code)
        except:
            self.send_error(500)
            print_exc()

    def do_DELETE(self):
        try:
            validation=get_validation(self.headers.get('Authorization'))
            path=Path(config['storage']['root'])
            path=path.joinpath(validation['user_id'])
            path=path.joinpath(validation['client_id'])
            while self.path[0] == '/' or self.path[0] == '\\':
                self.path=self.path[1:]
            path=path.joinpath(self.path)
            print(self.requestline+' > '+str(path))
            if(path.exists()):
                if(path.is_dir()):
                    path.rmdir()
                else:
                    path.unlink()
            self.send_response(204)
            self.send_header('content-length',0)
            self.end_headers()
        except:
            print_exc()
            self.send_error(500)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header('Access-Control-Allow-Headers', 'authorization,client-id')
        self.send_header('Access-Control-Allow-Methods','GET,POST,DELETE')
        self.end_headers()

    def end_headers(self):
        self.send_header('Access-Control-Allow-Origin','*')
        super().end_headers()
        return False

if __name__ == '__main__':
    if('broadcaster_id' not in config['api']):
        request_auth()
    bind=(config['network']['ip'],config['network'].getint('port'))
    server=ThreadingHTTPServer(bind,TS)
    run(['systemd-notify','--ready'])
    server.serve_forever()