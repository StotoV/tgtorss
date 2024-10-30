"""
Telegram to RSS web server

For detailed setup, deployment and run instructions see readme.md file

© 2021 MediaMonitoringBot, written by Maksym Trineiev
"""

import configparser
config = configparser.ConfigParser()
config.read('config.ini')

import logging
logging.basicConfig(
    filename=config['Logging']['FILE_NAME'],
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    level=int(config['Logging']['LEVEL']))

import base64
from feedgen.feed import FeedGenerator
from fastapi import FastAPI, Response, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from markdown2 import markdown
import pickle
from telethon import TelegramClient, sync
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import ImportChatInviteRequest
from uvicorn import run


client = TelegramClient(
    config['Telegram']['SESSION'],
    config['Telegram']['API_ID'],
    config['Telegram']['API_HASH'])

try:
    with open('hash.pickle', 'rb') as f:
        channel_hash = pickle.load(f)
    logging.info(f'Readed {len(channel_hash)} records from the hash')
except FileNotFoundError:
    channel_hash = dict()

templates = Jinja2Templates(directory='templates')
app = FastAPI()

@app.get('/', response_class=HTMLResponse)
async def home_page(request: Request):
    """
    Displays home page form templates/index.html
    """
    return templates.TemplateResponse('index.html', {'request': request})

@app.get('/channel/{channel_alias}')
async def create_rss(channel_alias: str, request: Request):
    """
    Get posts from the channel and return rss-feed
    """
    global channel_hash, client
    channel_alias = channel_alias.lstrip('@')
    private_channel = channel_alias[:8] == 'joinchat'
    if private_channel:
        private_hash = channel_alias[8:]
        channel_alias = 't.me/joinchat/' + private_hash
    try:
        await client.start()
        if channel_alias not in channel_hash:
            if private_channel:
                await client(ImportChatInviteRequest(private_hash))
            channel = await client.get_entity(channel_alias)
            ch_full = await client(GetFullChannelRequest(channel=channel))
            username = channel.username or channel.id
            channel_hash[channel_alias] = {
                'username': username,
                'title': channel.title,
                'id': channel.id,
                'about': ch_full.full_chat.about or str(username),
            }
            logging.info(f"Adding to the hash '{channel_alias}'")
            with open('hash.pickle', 'wb') as f:
                pickle.dump(channel_hash, f)
        ch = channel_hash[channel_alias]
        messages = [m async for m in client.iter_messages(
            ch['username'], limit=int(config['RSS']['RECORDS']))]
    except Exception as e:
        warn = f"{str(e)}, request: '{channel_alias}'"
        logging.warning(warn)
        return warn

    fg = FeedGenerator()
    fg.title(f"{ch['title']} (@{ch['username']}, id:{ch['id']})")
    fg.subtitle(ch['about'])
    link = channel_alias if private_channel else f"t.me/s/{ch['username']}"
    fg.link(href=f'https://{link}', rel='alternate')
    fg.generator(config['RSS']['GENERATOR'])
    fg.language(config['RSS']['LANGUAGE'])
    for m in messages:
        if not (config['RSS'].getboolean('SKIP_EMPTY') and not m.text):
            content = ''

            magic_numbers = [
                ('png', bytes([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])),
                ('jpg', bytes([0xFF, 0xD8, 0xFF])),
                ('jpg', bytes([0x00, 0x00, 0x00, 0x0C, 0x6A, 0x50, 0x20, 0x20, 0x0D, 0x0A, 0x87, 0x0A])),
                ('jpg', bytes([0xFF, 0x4F, 0xFF, 0x51])),
            ]
            blob = await client.download_media(m, bytes)
            ext = None
            if blob:
                for extension, magic_number in magic_numbers:
                    if blob.startswith(magic_number):
                        ext = extension
                        break

            if ext:
                b64blob = base64.b64encode(blob).decode('ascii')
                content += '<img src="data:image/'+ext+';base64,'+b64blob+'" />\n'

            fe = fg.add_entry(order='append')
            link = 'https://t.me/' + ('c/' if private_channel else '')
            fe.guid(guid=f"{link}{ch['username']}/{m.id}", permalink=True)
            content += markdown(m.text)
            fe.content(content)
            fe.published(m.date)

    logging.debug(f"Successfully requested '{ch['username']}'")
    return Response(content=fg.rss_str(), media_type='application/xml')


if __name__ == '__main__':
    run("main:app", host="0.0.0.0", port=8091, reload=True)
