import json
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from time import sleep
from typing import Counter, Optional

import requests
from lxml import etree
from retrying import retry

from gebooru import gebooru_downloader
from pickle_handler import PickleHandler


class ThreadsDownloader4chan:
    HEADER = {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/69.0.3497.92 Safari/537.36",
        "cookie": "__cfduid=d903e3abeaca2effe91e7b839a96be7211527491373; _ga=GA1.3.1213173136.1527491373; _ga=GA1.2.2716196.1533521826; _gid=GA1.2.2067292582.1537358233; _gid=GA1.3.2067292582.1537358233; Hm_lvt_ba7c84ce230944c13900faeba642b2b4=1537359428,1537361149,1537362700,1537363469; Hm_lpvt_ba7c84ce230944c13900faeba642b2b4=1537363858"
    }
    PROXIES = {
        "http": "socks5://127.0.0.1:10808",
        'https': 'socks5://127.0.0.1:10808'
    }

    def __init__(self, base_url: str, threads_num: Optional[int] = 999, target_formats: Optional[str] = None) -> None:
        self.base_url = base_url
        self.board = base_url.split('/')[-2]
        self.threads_num = threads_num
        self.target_formats = target_formats.split(',') if target_formats else target_formats
        self.pre_download_dict = dict()
        self.history_handler = PickleHandler('wallpaper.history')
        self.history_urls = self.history_handler.load()
        self.download_folder = './4chan_thread_download_folder'
        if not os.path.exists(self.download_folder):
            os.makedirs(self.download_folder)
        self.lock = threading.Lock()

    def set_total(self, n):
        self.counter = 0
        self.total = n

    def get_process(self):
        with self.lock:
            self.counter += 1
        return f"[{self.counter}/{self.total}]"

    @retry(wait_exponential_multiplier=1000, wait_exponential_max=60000)
    def request_get_with_retry(self, url, **args):
        default_args = {
            'headers': ThreadsDownloader4chan.HEADER,
            'proxies': ThreadsDownloader4chan.PROXIES
        }
        default_args.update(args)
        default_args = {i[0]: i[1] for i in default_args.items() if i[1]}
        return requests.get(url, **default_args)

    def history_filter(self):
        self.pre_download_list = []
        for thread_name, imgs_f_name in self.pre_download_dict.items():
            temp_imgs_f_name = []
            for img_f_name in imgs_f_name:
                if img_f_name[0] in self.history_urls:
                    continue
                elif (self.target_formats and img_f_name[0].lower().split('.')[-1] not in self.target_formats):
                    continue
                else:
                    temp_imgs_f_name.append(img_f_name)
                    self.history_urls.add(img_f_name[0])
            if temp_imgs_f_name:
                for temp_img_f_name in temp_imgs_f_name:
                    self.pre_download_list.append([thread_name, temp_img_f_name[0], temp_img_f_name[1]])
        self.history_handler.dump(self.history_urls)

    @property
    def url_catalog2thread(self):
        if self.base_url[-1] != '/':
            url = self.base_url + '/'
        else:
            url = self.base_url
        return url.replace('catalog', 'thread')

    def get_all_thread(self, base_url):
        r = self.request_get_with_retry(base_url)
        data = json.loads(re.findall(r"var catalog =(.*?);var style_group =", r.text)[0])
        threads_url = list(data['threads'].keys())
        self.threads_url = [self.url_catalog2thread+i for i in threads_url]
        self.threads_url = self.threads_url[:self.threads_num]

    def parse_thread_get_img_url(self, thread_url):
        try:
            r = self.request_get_with_retry(thread_url)
            thread_name = (
                re.findall(r'<title>(.*?)</title>', r.text)[0]
                .replace('|', '').replace('?', '').replace('*', '').replace('#', '').replace('\\', '')
                .replace('<', '').replace('>', '').replace(':', '').replace('“', '').replace('/', '')
                .split('-')[1].strip()
            )
            print(self.get_process(), 'Parsed thread:', thread_name)
            html = etree.HTML(r.content)
            imgs = html.xpath(".//a[@class='fileThumb']/@href")
            imgs = ['https:'+i for i in imgs]
            f_name = html.xpath(".//div[@class='fileText']/a/text()")
            imgs_f_name = list(zip(imgs, f_name))
            self.pre_download_dict.update({thread_name: imgs_f_name})
        except:
            print(traceback.format_exc())

    def run(self):
        try:
            self.get_all_thread(self.base_url)
            self.set_total(len(self.threads_url))
            with ThreadPoolExecutor(8) as executor:
                executor.map(self.parse_thread_get_img_url, self.threads_url)
            self.history_filter()
            self.set_total(len(self.pre_download_list))
            with ThreadPoolExecutor(16) as executor:
                executor.map(self.downloader, self.pre_download_list, self.board)
        except Exception as e:
            print(e)

    def downloader(self, items, board):
        try:
            print(self.get_process(), 'Downloading:', items)
            thread_name, img, f_name = items
            download_folder = os.path.join(self.download_folder, board, thread_name)
            with self.lock:
                if not os.path.exists(download_folder):
                    os.makedirs(download_folder)
            content = self.request_get_with_retry(img).content
            with open(os.path.join(download_folder, f_name), 'wb') as f:
                f.write(content)
        except:
            print(traceback.format_exc())

    def gebooru_run(self, if_gebooru_record_page_range):
        gebooru_page_his_handler = PickleHandler('gebooru.page.history')
        try:
            page_range = gebooru_page_his_handler.load().pop() + 1
        except Exception as e:
            print("get page_range Error:", e)
            page_range = 1
        # page_range = int(time.time()/3600)-451597
        if not if_gebooru_record_page_range:
            page_range = 100
        print("gebooru page_range:", page_range)
        gebooru_downloader(page_range, self.download_folder, self.history_urls, self.history_handler)
        if if_gebooru_record_page_range:
            gebooru_page_his_handler.dump({page_range})
        print("gebooru done ....")


if __name__ == "__main__":
    if_4chan = True
    if_gebooru = False
    if_gebooru_record_page_range = False
    for _ in range(12800):
        if if_4chan:
            four = ThreadsDownloader4chan('https://boards.4chan.org/hr/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/gif/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/aco/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/r/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/b/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/soc/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/s/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/hc/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/h/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/e/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/u/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/d/catalog', target_formats='gif')
            four.run()
            four = ThreadsDownloader4chan('https://boards.4chan.org/t/catalog', target_formats='gif')
            four.run()
        if if_gebooru:
            four = ThreadsDownloader4chan('https://boards.4chan.org/hr/gebooru')
            four.gebooru_run(if_gebooru_record_page_range)
        sleep(3600*10)
